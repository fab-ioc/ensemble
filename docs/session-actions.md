# Session actions: one inventory, one eligibility model

This file is the action/eligibility matrix the acceptance criteria ask for.
`static/actions.js` implements it; `tests/test_session_actions.py` checks it.

## Where the code was before

| Place | Function | Problem |
|---|---|---|
| `index.html` rows (legacy, `DETAIL_MODE` off) and raw-session docked panel | `actionsCell(r, isLive)` ~L3571 | a wall of ~12 buttons, no ⋯; Make PO silently omitted unless `!isLive && cwd && !roomId && !orphan` |
| `index.html` docked room panel | `detailActions` ~L4047 (End / Open / Delete) + `detailOverflow` ~L4071 (⋯ in `dp-head`) | Make PO silently omitted unless `makePoTaskOk` (~L8563) |
| `index.html` orphan panel | `detailActions` orphan branch | Open + Delete only |
| `session.html` pop-out | `<header>` buttons ~L1001–1016, wired ~L4205–4298, 4703–4738; visibility set in `refresh` ~L3389–3429 | separate button wall, different labels, missing Agents/Move/Make PO/Delete/Terminal colours |
| Terminal colours picker | `openThemeMenu` ~L10604 (index), `#theme` handler ~L4270 (session) | always below `getBoundingClientRect()`, no flip, no vertical clamp; the ⋯ menu closes and hides the item it was anchored to |

Click wiring in `index.html` is by class in one document listener (~L12762–13142):
`.rename-btn .auto-btn .moveproj-btn .makepo-btn .agents-btn .finder-btn .ij-btn
.theme-dd-trigger .chat-scheme-btn .room-delete .room-end .room-resume-inline
.open-btn .close-btn .archive-btn .delete-btn .terminal-btn .focus-btn .send-btn
.dp-terms-btn .dp-terms-hide .dp-pause-btn .orphan-open .orphan-delete`.
The ⋯ menu open/close is ~L12188–12211; Escape ~L12064; `DP_MENU_OPEN` ~L4266
defers the top-slot rewrite while a menu is open.

## How it is built

1. **`static/actions.js`** (new shared script, add to `dashboard.PAGE_FILES`,
   loaded by `index.html` and `session.html`). Pure functions, testable in Node:
   - `sessionActions(state, env)` → `{ primary, groups: [[item…]…] }`.
   - `makePoItem(state)`: the item from the hub's answer, `row.makePo`
     (`{ok, code, reason, fix, confirm?}`, task #87). The page writes no
     condition of its own; a row without `makePo` is "not known", never on.
   - `actionBarHtml(model, esc)`: primary button + `More actions` button +
     `role="menu"` list, groups split by `role="separator"`, danger group last.
     An enabled item carries its legacy action class (so `index.html`'s
     existing listener keeps working) and `data-act`; a disabled item has
     `aria-disabled="true"`, **no** action class, its reason in `title` and as
     a visible second line (`--fg-muted`), and stays focusable.
   - `popupPlace(anchorRect, size, viewport, opts)`: below/above flip, or
     beside (`side: 'right'`, falling back left, then below/above) for a
     submenu; clamps to all four edges with an 8px margin; returns `maxHeight`
     when neither side fits. Plus a DOM helper that re-places on
     `scroll` (capture) and `resize`.
   - `themeListHtml(names, favs, current, filter)` shared list markup.
2. **CSS** for `.am-*` is a marked identical block in both pages (pages keep
   their CSS); a test compares the two blocks.
3. **`index.html`**: `actionsCell` and the docked panel both render
   `actionBarHtml(sessionActions(...))`; the bar sits in `.dp-actions`; remove
   the old ⋯ from `dp-head` and the draft Start from the summary (one primary).
   Add `.am-menu:not([hidden])` to `DP_MENU_OPEN`. Menu keyboard: Enter / Space
   / ArrowDown open on the first item, ArrowUp on the last; arrows, Home and End
   move; Escape and Tab close; focus returns to the trigger. Outside click
   closes. Terminal colours opens its picker **beside the item, with the menu
   kept open**; the anchor falls back to the More button, then to the last
   rect, if the item goes away. Keep other users of `.ov-item` (the workspace
   context menu `.dcm`) untouched.
4. **`session.html`**: the header keeps the title, status and `⧉ Dock`, and
   replaces the button wall with the same bar from the same model. It acts
   in place for Terminals, Pause, Rename, Suggest a name, Folder, Terminal
   colours, Chat in terminal colours, End and Resume/Start. Agents and models,
   Move to project, Make PO, Delete task and Show in Workspace are sent to the
   dashboard: `window.opener.postMessage({type:'session-action', act, roomId})`
   when a same-origin opener is open, otherwise
   `window.open('/?task=<room>&act=<act>')`. `index.html` handles both (origin
   checked) through the same handlers; each one confirms in a dialog.
5. **Hub**: nothing new here. The pop-out reads `makePo` from its `/api/room`
   payload when the hub puts it there, and otherwise shows "not known"
   (#87: `/api/sessions` rows and `/api/room` both carry it, with its words, on every row).
6. **Tests**: new `tests/test_session_actions.py` (Node): the matrix below,
   docked/pop-out parity (the same state gives the same model, and both pages
   render with `actionBarHtml`), Make PO shown from `row.makePo` (on, off
   with its reason, not known), and `popupPlace` at 1280×800 and
   360×640 near the bottom/right edges and after scrolling. Update
   `test_links.py` `HubMachineActions` (uses `actionsCell`/`detailOverflow`) and
   `test_make_po.py::test_where_the_page_offers_it` (regex on the old markup),
   and `test_send_resumes.py` L680 (`$('#resume').hidden`).

## Inputs

`state`: `kind` (`raw` | `room` | `orphan`), `sessionId`, `roomId`, `cwd`,
`pid`, `agent`, `label`, `live`, `draft`, `status`, `archived`, `members`,
`makePo` (the hub's answer), `currentTheme`, `chatSchemeOn`, `resuming`.
For a room, `status` is its lifecycle (`active` | `paused` | `waiting_human`)
and `resuming` is a resume under way that has not failed. The pop-out reads
them from `/api/room` (`status`, `pending`); the dashboard from the
`/api/sessions` row's `roomStatus` and `resuming`, not its `status`, which
is only the busy/idle dot.
`env`: `hub` (`onHubMachine()`), `features` (`focus`, `themes`, `send`,
`geometry`), `terminalName`, `fileManagerName`. **No view parameter**: docked
and pop-out get the same model by construction.

## Primary action

| State | Primary |
|---|---|
| raw, running in a terminal | Focus (on the hub with `features.focus`), else none |
| raw, history | Open (headless) |
| room, draft | Start |
| room, stopped | Resume |
| room, running | End (Default variant: it keeps the session) |
| orphan | Open |

## More actions: groups in a fixed order

Items not listed for a state are left out. **(off: reason)** means shown
disabled with that reason.

**1. Run**

| Item | raw live | raw history | room running | room stopped / draft | orphan |
|---|---|---|---|---|---|
| Show terminals (hub) / Terminal + Hide terminal (other computer) | – | – | ✓ | – | – |
| Pause / Resume | – | – | ✓ | – | – |
| Focus (when not primary) | off: “Only on the hub's own screen” / “{T} windows can't be raised here” | – | – | – | – |
| Send | ✓ with `features.send`, else off: “Typing into a terminal session isn't supported here” | – | – | – | – |
| Open in a {T} window (hub) / Resume here with its terminal | – | ✓ with cwd, else off: “No working folder recorded” | – | – | – |

**2. Organise**

| Item | raw | room | orphan |
|---|---|---|---|
| Rename | ✓ | ✓ | – |
| Suggest a name | ✓ | ✓ | – |
| Agents and models | – | ✓; off while running: “Running: end it first, then reassign” | – |
| Move to project | ✓ | ✓ | – |
| Make PO of a new project… | see below | see below | off: orphan reason |
| Archive / Unarchive | history only | – | – |

**3. Folder and colours**

| Item | raw | room |
|---|---|---|
| Open in {FM} (hub) / Browse the folder | cwd, else off: “No working folder recorded” | same |
| Open in editor (hub) / Show in Workspace | ✓ | cwd, else off |
| Terminal colours… | `themes` and (pid or cwd); else off: no folder / “{T} here has no colour schemes” | `themes` and cwd, same reasons |
| Chat in terminal colours (checkbox) | – | `themes` and cwd, same reasons |

**4. Destructive** (separated by a rule, always last)

| State | Item |
|---|---|
| raw live | Close session (confirms) |
| raw history | Delete session |
| room stopped / draft | Delete task |
| room running | Delete task, off: “Running: end it first” |
| orphan | Delete |

## Make PO of a new project…

Always shown, in the Organise group. On exactly when the hub's `makePo.ok` is
true. Off, its visible reason is `makePo.reason` followed by `makePo.fix`; the
codes (`live`, `recent`, `held`, `other_project`, `is_po`, `draft`, `agents`,
`unsupported`, `orphan`, `no_cwd`, `has_po`) are the hub's to word. A row
without `makePo` shows "Not known yet whether it can become a PO: the hub has
not said." A request that comes from the pop-out is checked against the row's
own answer again before the dialog opens.
