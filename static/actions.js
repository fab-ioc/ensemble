// The actions a session offers, shared by the dashboard (its task panel and its
// list rows) and the popped-out /session window. docs/session-actions.md is the
// matrix this implements.
//
// One model, one renderer, one placement rule:
//   sessionActions(state, env)  what can be done, in which group, and when an
//                               action cannot be done, why. No view parameter:
//                               the panel and the pop-out get the same answer
//                               by construction.
//   actionBarHtml(model)        one primary action and a "More" menu.
//   popupPlace(anchor, ...)     where a popup goes: beside or under its trigger,
//                               flipped when it does not fit, inside the window.
// Each page wires the actions themselves: the dashboard by the classes an item
// carries (its document click handler), the pop-out by data-act.
// Everything above "DOM" runs in Node too (tests/test_session_actions.py).
(function (root) {
'use strict';

const escH = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const NO_FOLDER = 'No working folder is recorded for it.';
const UNKNOWN_PO = 'Not known yet whether it can become a PO: the hub has not said.';

// Make PO of a new project…: shown always. Whether it may, and why not, is the
// hub's answer (the row's makePo); a row without one is not known, not yes.
function makePoItem(s) {
  const base = {
    id: 'makepo', label: 'Make PO of a new project…', cls: 'makepo-btn',
    title: 'Start a new project with this conversation as its PO: the task you talk to about the project, which its other tasks report to',
    data: s.kind === 'room' ? { room: s.roomId } : { sid: s.sessionId },
  };
  const mp = s.makePo;
  if (!mp || typeof mp !== 'object') return { ...base, disabled: true, reason: UNKNOWN_PO };
  if (mp.ok) return base;
  const why = [mp.reason, mp.fix].filter(x => typeof x === 'string' && x.trim()).join(' ');
  return { ...base, disabled: true, reason: why || 'It cannot become a PO.' };
}

// state: kind ('raw' | 'room' | 'orphan' | 'past'), sessionId, roomId, cwd, pid, agent,
//   label, live, draft, status, archived, members, makePo, currentTheme,
//   chatSchemeOn, resuming.
// env: hub (this browser is on the hub machine), features {focus, themes,
//   send, geometry}, terminalName, fileManagerName.
function sessionActions(s, env) {
  s = s || {}; env = env || {};
  const f = env.features || {};
  const hub = !!env.hub;
  const T = env.terminalName || 'terminal', FM = env.fileManagerName || 'file manager';
  const sid = s.sessionId || '', rid = s.roomId || '', cwd = s.cwd || '';
  const live = !!s.live;
  const run = [], org = [], folder = [], danger = [];
  const off = (item, reason) => ({ ...item, disabled: true, reason });
  let primary = null;

  const rename = { id: 'rename', label: 'Rename…', cls: 'rename-btn', data: { sid }, title: 'Change the name it is shown by' };
  const auto = { id: 'auto', label: 'Suggest a name', cls: 'auto-btn', data: { sid }, title: 'Ask Claude to suggest a name from the conversation' };
  const move = { id: 'moveproj', label: 'Move to project…', cls: 'moveproj-btn', data: { sid }, title: 'File it under a project, or take it out of one' };
  const finder = cwd
    ? { id: 'finder', label: hub ? `Open in ${FM}` : 'Browse the folder', cls: 'finder-btn', data: { path: cwd },
        title: hub ? `Open its working folder in ${FM}` : 'Browse its working folder in the file viewer' }
    : off({ id: 'finder', label: hub ? `Open in ${FM}` : 'Browse the folder', title: '' }, NO_FOLDER);
  const ideItem = { id: 'ide', label: hub ? 'Open in editor' : 'Show in Workspace', cls: 'ij-btn', data: { sid },
    title: hub ? 'Open the project in its preferred editor (found from its language)' : 'Show its files in the Workspace tab' };
  const colours = (key) => {
    const cur = s.currentTheme || '';
    const item = { id: 'colours', label: cur ? `Terminal colours: ${cur}` : 'Terminal colours…', cls: 'theme-dd-trigger',
      data: { pid: key.pid || '', cwd, current: cur }, keepOpen: true, popup: true,
      title: `Pick the ${T} colour scheme for its folder` };
    if (!f.themes) return off(item, `${T} here has no colour schemes.`);
    if (!key.pid && !cwd) return off(item, NO_FOLDER);
    return item;
  };

  if (s.kind === 'past') {
    // A conversation a task's seat left behind (a PO's rotation, a switch, a
    // handover): read only. Continuing it would split the seat in two, and
    // moving, archiving or deleting it would take it from its task.
    folder.push(finder);
  } else if (s.kind === 'orphan') {
    primary = { id: 'open', label: 'Open', variant: 'primary', cls: 'orphan-open', data: { grp: sid }, title: 'Resume this collaboration headless' };
    org.push(makePoItem(s));
    danger.push({ id: 'delete', label: 'Delete…', danger: true, cls: 'orphan-delete', data: { grp: sid },
      title: 'Delete every agent transcript and the ~/cs scratch folder' });
  } else if (s.kind === 'room') {
    const room = { room: rid };
    if (s.draft) primary = { id: 'start', label: 'Start', variant: 'primary', cls: 'room-resume-inline', data: room, title: 'Launch the agent(s) with the task spec' };
    else if (live) primary = { id: 'end', label: 'End', variant: 'default', cls: 'room-end', data: room, title: 'Stop the agent(s) and keep the session' };
    else primary = { id: 'resume', label: 'Resume', variant: 'primary', cls: 'room-resume-inline', data: room, title: 'Relaunch the agent(s) with their conversation' };
    if (s.resuming && !live) primary = { ...primary, disabled: true, reason: 'Starting up…' };
    if (live) {
      // On the hub this shows or hides the agents' terminals. From another
      // computer it is Terminal: the terminal the hub captures, brought to the
      // front when pressed again, so hiding it is an item of its own (shown
      // only while the terminal is; the page sets that as the menu opens).
      if (hub) run.push({ id: 'terminals', label: 'Show terminals', cls: 'dp-terms-btn', title: 'Show or hide the agents’ terminals' });
      else run.push({ id: 'terminals', label: 'Terminal', cls: 'dp-terms-btn', title: 'The agent’s terminal, as the hub captures it. Pressed again, it comes to the front.' },
                    { id: 'terms-hide', label: 'Hide terminal', cls: 'dp-terms-hide', hidden: true, title: 'Hide the agent’s terminal' });
      const paused = s.status === 'paused';
      run.push({ id: 'pause', label: paused ? 'Resume' : 'Pause', cls: 'dp-pause-btn', data: { room: rid, status: s.status || '' },
        title: paused ? 'Let the agents carry on' : 'Hold the agents where they are' });
    }
    org.push(rename, auto);
    const agents = { id: 'agents', label: 'Agents and models…', cls: 'agents-btn', data: room, title: 'Change who is assigned to this task and which model each one runs' };
    org.push(live ? off(agents, 'Running: end it first, then reassign.') : agents);
    org.push(move, makePoItem(s));
    folder.push(finder, cwd ? ideItem : off(ideItem, NO_FOLDER), colours({}));
    const on = !!(s.chatSchemeOn && cwd);
    const chat = { id: 'chat-colours', label: 'Chat in terminal colours', role: 'menuitemcheckbox', checked: on,
      cls: 'chat-scheme-btn', data: { cwd, on: on ? 1 : 0 },
      title: on ? 'The chat uses its folder’s terminal colours. Click to follow the dashboard theme.'
                : 'The chat follows the dashboard theme. Click to use its folder’s terminal colours instead.' };
    folder.push(!f.themes ? off(chat, `${T} here has no colour schemes.`) : !cwd ? off(chat, NO_FOLDER) : chat);
    const del = { id: 'delete', label: 'Delete task…', danger: true, cls: 'room-delete', data: room,
      title: 'Delete its transcripts, its record and its ~/cs scratch folder' };
    danger.push(live ? off(del, 'Running: end it first.') : del);
  } else {
    // A conversation the hub lists but does not run: in a terminal, or history.
    const pid = s.pid || '';
    if (live) {
      const focus = { id: 'focus', label: 'Focus', variant: 'primary', cls: 'focus-btn', data: { pid }, title: `Bring its ${T} window to the front` };
      if (hub && f.focus && pid) primary = focus;
      else run.push(off({ ...focus, variant: undefined }, !hub ? 'Only on the hub’s own screen: this computer cannot raise its windows.'
                                                            : `${T} windows cannot be raised on this system.`));
      const send = { id: 'send', label: 'Send a message…', cls: 'send-btn', data: { pid, agent: s.agent || '' }, title: 'Type a message into this running session and submit it' };
      run.push(f.send && pid ? send : off(send, 'Typing into a terminal session is not supported on this system.'));
    } else {
      primary = { id: 'open', label: 'Open', variant: 'primary', cls: 'open-btn', title: 'Open in the dashboard, headless: no terminal window',
        data: { sid, cwd, agent: s.agent || '', label: s.label || '' } };
      const term = { id: 'terminal', label: hub ? `Open in a ${T} window` : 'Resume here with its terminal', cls: 'terminal-btn',
        data: { sid, cwd, agent: s.agent || '', label: s.label || '' },
        title: hub ? `Open in a real ${T} terminal window` : 'Resume it here, headless, and show its terminal' };
      run.push(cwd ? term : off(term, NO_FOLDER));
    }
    org.push(rename, auto, move, makePoItem(s));
    if (!live) org.push(s.archived
      ? { id: 'archive', label: 'Unarchive', cls: 'archive-btn', data: { sid, archived: 1 }, title: 'Put it back in the main list' }
      : { id: 'archive', label: 'Archive', cls: 'archive-btn', data: { sid, archived: 0 }, title: 'Hide it from the main list (reversible)' });
    folder.push(finder, ideItem, colours({ pid }));
    if (live) danger.push({ id: 'close', label: 'Close session…', danger: true, cls: 'close-btn', data: { pid },
      title: f.geometry ? `Close its ${T} window and end it (the window position is kept)` : `Close its ${T} window and end it` });
    else danger.push({ id: 'delete', label: 'Delete session…', danger: true, cls: 'delete-btn', data: { sid },
      title: 'Delete its transcript and the dashboard’s notes on it for good' });
  }
  // A disabled item carries no action class: the page's handlers never see it.
  const clean = it => it.disabled ? { ...it, cls: undefined, data: {} } : it;
  return {
    primary: primary && clean(primary),
    groups: [run, org, folder, danger].map(g => g.map(clean)).filter(g => g.length),
  };
}

function dataAttrs(d) {
  return Object.entries(d || {}).filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => ` data-${k}="${escH(v)}"`).join('');
}

function itemHtml(it) {
  const cls = ['am-item', it.danger ? 'danger' : '', it.disabled ? '' : (it.cls || '')].filter(Boolean).join(' ');
  const tip = it.disabled ? it.reason : it.title;
  return `<button type="button" role="${it.role || 'menuitem'}" class="${cls}" data-act="${escH(it.id)}"${it.disabled ? '' : dataAttrs(it.data)}`
    + (tip ? ` title="${escH(tip)}"` : '')
    + (it.disabled ? ' aria-disabled="true"' : '')
    + (it.role === 'menuitemcheckbox' ? ` aria-checked="${!!it.checked}"` : '')
    + (it.keepOpen ? ' data-am-keep="1"' : '')
    + (it.popup ? ' aria-haspopup="dialog"' : '')
    + (it.hidden ? ' hidden' : '') + '>'
    + `<span class="am-lbl">${it.role === 'menuitemcheckbox' ? `<span class="am-check" aria-hidden="true">${it.checked ? '✓' : ''}</span>` : ''}${escH(it.label)}</span>`
    + (it.disabled ? `<span class="am-why">${escH(it.reason)}</span>` : '')
    + '</button>';
}

function actionBarHtml(model) {
  const p = model && model.primary;
  const primary = p
    ? `<button type="button" class="am-btn am-primary${p.variant === 'primary' ? ' is-primary' : ''}${p.disabled ? '' : ' ' + p.cls}" data-act="${escH(p.id)}"`
      + (p.disabled ? '' : dataAttrs(p.data))
      + ` title="${escH(p.disabled ? p.reason : p.title)}"${p.disabled ? ' aria-disabled="true"' : ''}>${escH(p.label)}</button>`
    : '';
  const groups = (model && model.groups) || [];
  const menu = groups.length
    ? `<span class="am-wrap"><button type="button" class="am-btn am-more" aria-haspopup="menu" aria-expanded="false" aria-label="More actions" title="More actions">`
      + `<span class="am-dots" aria-hidden="true">⋯</span> More</button>`
      + `<div class="am-menu" role="menu" aria-label="More actions" hidden>`
      + groups.map(g => g.map(itemHtml).join('')).join('<div class="am-sep" role="separator"></div>')
      + '</div></span>'
    : '';
  return `<div class="am-bar">${primary}${menu}</div>`;
}

// Where a popup goes. anchor: the trigger's client rect; size: the popup's
// natural {width, height}; vp: the visible part of the window, {width, height}
// and its {left, top} in client coordinates (a phone's keyboard or pinch zoom
// shrinks and shifts it; 0 otherwise). side 'below'
// (default) opens under the trigger and flips above when there is more room
// there; 'beside' opens to its right, else its left, else as 'below'. align
// 'end' lines the popup's right edge up with the trigger's. Always inside the
// window with `margin` to spare; too tall for the side it takes, it gets a
// maxHeight and scrolls.
function popupPlace(anchor, size, vp, o) {
  o = o || {};
  const m = o.margin ?? 8, gap = o.gap ?? 4;
  const W = vp.width, H = vp.height, X = vp.left || 0, Y = vp.top || 0;
  // Worked out against the visible part, then put back in client coordinates.
  anchor = { left: anchor.left - X, right: anchor.right - X, top: anchor.top - Y, bottom: anchor.bottom - Y };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
  const w = Math.min(size.width, W - 2 * m);
  let h = size.height, maxHeight = null, placement, left, top;
  const roomRight = W - m - (anchor.right + gap), roomLeft = anchor.left - gap - m;
  if (o.side === 'beside' && (w <= roomRight || w <= roomLeft)) {
    placement = w <= roomRight ? 'right' : 'left';
    left = placement === 'right' ? anchor.right + gap : anchor.left - gap - w;
    if (h > H - 2 * m) { h = H - 2 * m; maxHeight = h; }
    top = clamp(anchor.top, m, H - m - h);
  } else {
    const below = H - m - (anchor.bottom + gap), above = anchor.top - gap - m;
    placement = (h <= below || below >= above) ? 'below' : 'above';
    const room = Math.max(0, placement === 'below' ? below : above);
    if (h > room) { h = Math.max(room, Math.min(size.height, 120)); maxHeight = h; }
    top = placement === 'below' ? anchor.bottom + gap : anchor.top - gap - h;
    left = o.align === 'end' ? anchor.right - w : anchor.left;
    top = clamp(top, m, Math.max(m, H - m - h));
  }
  left = clamp(left, m, Math.max(m, W - m - w));
  return { left: Math.round(left + X), top: Math.round(top + Y), width: w < size.width ? w : null, maxHeight, placement };
}

// The theme picker's list: favourites first, then the rest, each a star and a
// name, both buttons (the arrows move between names).
function themeListHtml(names, favs, current, filter) {
  const q = String(filter || '').toLowerCase().trim();
  const fav = new Set(favs || []);
  const matches = (names || []).filter(n => !q || n.toLowerCase().includes(q));
  const f = matches.filter(n => fav.has(n)).sort((a, b) => a.localeCompare(b));
  const rest = matches.filter(n => !fav.has(n));
  const row = n => `<div class="theme-dd-item${n === current ? ' current' : ''}" data-theme="${escH(n)}">`
    + `<button type="button" class="star${fav.has(n) ? ' is-fav' : ''}" aria-pressed="${fav.has(n)}" title="${fav.has(n) ? 'Remove from favourites' : 'Add to favourites'}" aria-label="${fav.has(n) ? 'Remove ' + escH(n) + ' from favourites' : 'Add ' + escH(n) + ' to favourites'}">${fav.has(n) ? '★' : '☆'}</button>`
    + `<button type="button" class="name" title="${escH(n)}"${n === current ? ' aria-current="true"' : ''}>${escH(n)}</button></div>`;
  let body = '';
  if (f.length) body += '<div class="theme-dd-section">Favourites</div>' + f.map(row).join('');
  if (rest.length) body += `<div class="theme-dd-section">${f.length ? 'All schemes' : 'Schemes'}</div>` + rest.map(row).join('');
  return body || '<div class="theme-dd-empty">No scheme matches.</div>';
}

const api = { sessionActions, actionBarHtml, popupPlace, themeListHtml, makePoItem, UNKNOWN_PO };

// ---- DOM -------------------------------------------------------------------
// One menu open at a time. While it is open it is moved to <body> (the task
// panel slides with a transform, and the pop-out's header scrolls sideways on a
// phone: either would carry or clip a fixed popup), and put back when it
// closes. A popup opened from one of its items (the theme picker) registers as
// its child: a click in it is not "outside", and it stays where it is if the
// menu closes first.
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  const S = { open: null, children: new Set() };
  // What is on screen: the visual viewport where there is one (it shrinks when
  // a phone's keyboard opens, and scrolls when zoomed), else the window.
  const viewport = () => {
    const v = window.visualViewport;
    if (v && v.width && v.height) return { width: v.width, height: v.height, left: v.offsetLeft, top: v.offsetTop };
    return { width: document.documentElement.clientWidth || innerWidth,
             height: document.documentElement.clientHeight || innerHeight, left: 0, top: 0 };
  };
  const shown = el => !!(el && el.isConnected && el.getClientRects().length);

  // Put `el` against what `anchor()` returns (a rect, or null when there is no
  // anchor any more). Re-placed on scroll anywhere and on resize.
  function place(el, rect, o) {
    el.style.maxHeight = ''; el.style.width = '';
    el.style.left = '0px'; el.style.top = '0px';
    const size = { width: el.offsetWidth, height: el.offsetHeight };
    const p = popupPlace(rect, size, viewport(), o);
    el.style.left = p.left + 'px'; el.style.top = p.top + 'px';
    if (p.width) el.style.width = p.width + 'px';
    if (p.maxHeight) el.style.maxHeight = p.maxHeight + 'px';
    el.dataset.placement = p.placement;
    return p;
  }
  function track(el, anchor, o, lost) {
    let last = null;
    const go = () => {
      const r = anchor();
      if (!r) { if (lost && lost() === false) return; if (!last) return; }
      last = r || last;
      place(el, last, o);
    };
    go();
    const opt = { capture: true, passive: true };
    // Not its own scrolling (a long list inside it). A scroll of the window
    // itself has the window as its target, which is not a node.
    const onScroll = ev => { const t = ev.target; if (!(t instanceof Node && el.contains(t))) go(); };
    window.addEventListener('scroll', onScroll, opt);
    window.addEventListener('resize', go);
    if (window.visualViewport) { visualViewport.addEventListener('resize', go); visualViewport.addEventListener('scroll', go); }
    return { update: go, stop() {
      window.removeEventListener('scroll', onScroll, opt);
      window.removeEventListener('resize', go);
      if (window.visualViewport) { visualViewport.removeEventListener('resize', go); visualViewport.removeEventListener('scroll', go); }
    } };
  }

  const items = menu => [...menu.querySelectorAll('.am-item')].filter(b => !b.hidden);
  function openMenu(trigger, focus) {
    closeMenu();
    const menu = trigger.parentElement && trigger.parentElement.querySelector('.am-menu');
    if (!menu) return;
    if (typeof api.onOpen === 'function') { try { api.onOpen(menu, trigger); } catch (e) {} }
    menu._amHome = trigger.parentElement;
    document.body.appendChild(menu);
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    const t = track(menu, () => shown(trigger) ? trigger.getBoundingClientRect() : null, { align: 'end' },
                    () => { closeMenu(); return false; });
    S.open = { trigger, menu, t };
    const list = items(menu);
    if (focus === 'first' && list[0]) list[0].focus();
    else if (focus === 'last' && list.length) list[list.length - 1].focus();
  }
  function closeMenu(o) {
    const cur = S.open;
    if (!cur) return;
    S.open = null;
    cur.t.stop();
    cur.menu.hidden = true;
    cur.trigger.setAttribute('aria-expanded', 'false');
    if (cur.menu._amHome && cur.menu._amHome.isConnected) cur.menu._amHome.appendChild(cur.menu);
    else cur.menu.remove();
    if (o && o.focus && shown(cur.trigger)) cur.trigger.focus({ preventScroll: true });
    // A popup opened from one of its items stays, against the More button now.
    S.children.forEach(c => { if (c._amUpdate) c._amUpdate(); });
    if (typeof api.onClose === 'function') { try { api.onClose(cur.menu, cur.trigger); } catch (e) {} }
  }
  const inChild = t => [...S.children].some(c => c.isConnected && c.contains(t));

  // Registered before the pages' own listeners (this script loads first), so
  // it runs first: a disabled item is stopped here and never reaches them.
  document.addEventListener('click', ev => {
    const t = ev.target;
    const more = t.closest && t.closest('.am-more');
    if (more) {
      ev.stopImmediatePropagation();
      if (S.open && S.open.trigger === more) closeMenu();
      else openMenu(more, ev.detail === 0 ? 'first' : null);
      return;
    }
    const btn = t.closest && t.closest('.am-item, .am-primary');
    if (btn && btn.getAttribute('aria-disabled') === 'true') {
      ev.preventDefault(); ev.stopImmediatePropagation();
      return;
    }
    if (btn && btn.classList.contains('am-item')) {
      if (!btn.dataset.amKeep) closeMenu({ focus: true });
      return;
    }
    if (S.open && !S.open.menu.contains(t) && !inChild(t)) closeMenu();
  });
  document.addEventListener('keydown', ev => {
    const t = ev.target;
    const onTrigger = t.closest && t.closest('.am-more');
    if (onTrigger && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      openMenu(onTrigger, ev.key === 'ArrowDown' ? 'first' : 'last');
      return;
    }
    if (!S.open) return;
    if (inChild(t)) return;              // the child popup has its own keys
    const menu = S.open.menu;
    if (ev.key === 'Escape') {
      ev.preventDefault(); ev.stopImmediatePropagation();
      closeMenu({ focus: true });
      return;
    }
    if (!menu.contains(t)) return;
    if (ev.key === 'Tab') { ev.preventDefault(); closeMenu({ focus: true }); return; }
    const list = items(menu);
    const i = list.indexOf(t);
    let j = -1;
    if (ev.key === 'ArrowDown') j = (i + 1) % list.length;
    else if (ev.key === 'ArrowUp') j = (i - 1 + list.length) % list.length;
    else if (ev.key === 'Home') j = 0;
    else if (ev.key === 'End') j = list.length - 1;
    if (j >= 0 && list[j]) { ev.preventDefault(); list[j].focus(); }
  }, true);

  Object.assign(api, {
    place, track, openMenu, closeMenu,
    isOpen: () => !!S.open,
    openIn: el => !!(S.open && el && (el.contains(S.open.trigger))),
    // A popup opened from an item of the open menu: beside that item, else
    // (the menu closed, or the item went away) against the More button, else
    // where it last was. Returns a handle: stop() when the popup closes.
    child(popup, item) {
      S.children.add(popup);
      const trigger = S.open && S.open.menu.contains(item) ? S.open.trigger : null;
      const anchor = () => shown(item) ? item.getBoundingClientRect()
        : shown(trigger) ? trigger.getBoundingClientRect() : null;
      // An item scrolled out of a long menu's view is brought into it first.
      if (shown(item) && item.closest('.am-menu')) item.scrollIntoView({ block: 'nearest' });
      const opts = { side: shown(item) && item.closest('.am-menu') ? 'beside' : 'below' };
      // Beside its item while the menu is open; under the More button after.
      const t = track(popup, () => { opts.side = shown(item) && item.closest('.am-menu') ? 'beside' : 'below'; return anchor(); }, opts);
      popup._amUpdate = t.update;
      return { update: t.update, stop() { t.stop(); S.children.delete(popup); delete popup._amUpdate; },
               returnFocus() { (shown(item) ? item : shown(trigger) ? trigger : null)?.focus({ preventScroll: true }); } };
    },
    // Arrow keys through a list of buttons (the theme picker's names).
    arrowKeys(ev, list) {
      const i = list.indexOf(document.activeElement);
      let j = -1;
      if (ev.key === 'ArrowDown') j = i < 0 ? 0 : Math.min(i + 1, list.length - 1);
      else if (ev.key === 'ArrowUp') j = i <= 0 ? -1 : i - 1;
      else return false;
      ev.preventDefault();
      if (j >= 0 && list[j]) list[j].focus();
      return j;
    },
  });
}

root.SessionActions = api;
if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
