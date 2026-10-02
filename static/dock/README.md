# Dock

A small browser library for docked, tabbed, floating, unpinned and popped-out panels, with the theme and layout that go
with them. Plain ES modules, no build step, no runtime dependencies.

Shared by two apps:

- **opTen console** (`opTen-microservices/stream-bridge`), where it started as `app/js/dock.js`.
- **Ensemble** (`claude-dashboard-main`), whose PO screen will be rebuilt on it.

Each app picks up a tagged version when it chooses. Changes to the library are made here, not in the apps.

The panels work as IntelliJ IDEA's tool windows do (v0.5.0, see "Title bar and view modes"): a title bar holds its
tabs, **⋯** (Options: View Mode, Move To, Take Screenshot, Maximise, Hide) and **−** (Hide), and a panel keeps its side whatever its view
mode.

What a person can do with the panels: drag a tab to an edge of the dock or of another panel (split), into another panel
(a tab), float it in a window inside the page, unpin it to a strip on its edge (it slides out on hover or click),
minimise or maximise a stack (a click on a minimised one's title bar restores it, a double click maximises it), hide
and show panels from a Panels menu, resize with the splitters (double-click one for
the default size, arrow keys move a focused one), pop a panel out into a browser window of its own, and reset the layout.
The layout is saved and comes back on reload.

## Try it

```sh
npm install
npm run demo          # http://127.0.0.1:5178/demo/
npm test              # Node tests, then the headless-Chrome tests
```

The demo has six dummy panels: a counter the main window updates four times a second, a form whose Send asks to confirm
in a dialog, a 300-row list with a filter, notes (a tab beside the form), a page in an `<iframe>` (click it and type in it,
then move other panels around: it does not reload) and a log of what the dock did. The top bar has Install app (when the
browser offers it), the Panels menu, a narrow switch (one column of tabs, as on a phone; a window under 640 px starts
narrow), Reset layout and the theme picker (every colour set, each with a swatch). `?narrow` starts narrow, `?pophtml`
pops panels out without `popout.html` (`popHtml`). `?toolstrip=1` is Ensemble's layout A: the List on the left and the
Counter in the middle, fixed, and Form, Notes, Page and Log as tools on a right strip 44 px wide (see "The tool strip"). The demo is installable: installed, it and its pop-out windows
open without an address bar.

## Install

**Recommended: a git submodule**, pinned to a tag, served as static files by the app:

```sh
git submodule add https://github.com/fab-ioc/dock.git static/dock
git -C static/dock checkout v0.1.0
```

```js
import { createDock, panelsFrom, createTheme } from './dock/src/index.js';
```

```html
<link rel="stylesheet" href="dock/css/theme.css">
<link rel="stylesheet" href="dock/css/dock.css">
```

A submodule keeps the version explicit in the app's own history and needs no build step or registry. Copying `src/` and
`css/` works too (but loses the link to this repo); importing from a URL is possible but puts a second origin in the
page, and the pop-out page must be on the app's own origin anyway.

## A minimal example

```html
<div id="dock-root">
  <section data-panel="list" data-title="List">…</section>
  <section data-panel="main" data-title="Main">…</section>
  <section data-panel="log"  data-title="Log">…</section>
</div>
<script type="module">
  import { createDock, panelsFrom, stack, split, createTheme } from './dock/src/index.js';

  createTheme();                                    // light / dark, remembered; see "Theme"
  const root = document.getElementById('dock-root');
  const dock = createDock({
    root,
    panels: panelsFrom(root),                       // [{ id, title, help, el }]
    storageKey: 'myapp.layout',
    popUrl: 'dock/src/popout.html',                 // see "The pop-out contract"
    fill: 'main',
    defaultLayout: split('row', [
      stack(['list'], { size: 260 }),
      split('col', [stack(['main']), stack(['log'], { size: 180 })]),
    ]),
  });
</script>
```

The dock moves the panels' own elements into its layout. What a panel shows stays the app's: the dock never re-creates
or clones a panel, so its listeners and state go wherever it goes.

## API

`createDock(options)` returns the dock:

| Method | What it does |
|---|---|
| `layout()`, `config()` | the current layout (plain JSON) and the config made from the options |
| `isShown(id)`, `isVisible(id)`, `isAuto(id)`, `isOut(id)`, `frontOf(id)` | where a panel is |
| `openFly(id)`, `closeFly()`, `flyOpen()` | slide an unpinned panel out, slide the open one back in, the one out (or `null`) |
| `activate(id)`, `reveal(id)` | bring a panel to the front of its stack; `reveal` also shows a hidden one, slides out an unpinned one, focuses its window, and brings one out with no window (after a reload) back where it was, seen |
| `moveTo(id, target, side)`, `dockEdge(id, side)` | dock a panel beside a stack (`{ kind: 'stack', stack }`) or at the dock's edge; side `left right top bottom center` |
| `viewMode(id)`, `setViewMode(id, mode)` | a panel's view mode, IntelliJ's: `'pinned'` (Dock Pinned), `'unpinned'` (Dock Unpinned), `'undock'`, `'float'`, `'window'` (or `'hidden'`); change it, the side kept (false when nothing changed or `can` forbids it) |
| `side(id)`, `moveSide(id, side)` | a panel's side (`left right top bottom`) in any mode; move it to another side in the mode it has (Move To), as deep as it last was on that axis (see "Title bar and view modes") |
| `moveStrip(id, index, side?)` | a strip panel to place `index` (from 0) of its strip, or of the strip on `side` in the mode it has, as a drag of its button does; saved, `onChange` told; false when it is not on a strip, already there, or `index` is not a number (a string of digits counts as one) (v0.8.0) |
| `float(id)`, `dockBack(id)`, `unpin(id)`, `pin(id)` | float in the page and back; unpin to the strip on its side and back (pinned, it docks on its strip's side) |
| `toggleMin(id)`, `toggleMax(id)`, `restoreMax()`, `maximised()` | minimise / maximise its stack |
| `setVisible(id, on)`, `showPanel(id)` | hide or show a panel; showing a hidden Window reopens its window |
| `popOut(id)`, `popIn(id)`, `popWindow(id)`, `isOpenOut(id)` | a browser window of its own, and back where it was; its window; whether that window is open now (not after a reload) |
| `setBadge(id, text, title)` | a small badge on the panel's tab, and on its strip button while it is unpinned (`null` removes it) |
| `setTitle(id, title)`, `title(id)` | a new title for a panel, everywhere it shows: its tab and tooltip, title bar, strip button (label and tooltip), the tab's ×, the ▾ tab list, the Panels menu, the ⋯ menu's name and the screen-reader names that say it, and its window's `document.title` (through `popTitle`, which gets the new title). Text, never markup; `null` gives its id; an id the dock does not have is nothing. The app owns it: the layout does not keep it (after a reload a panel has the title it is given). It stays through moves, pop-out and back, narrow, and `removePanel` → `addPanel` with the same object. A panel given to `createDock` is that object, so its `title` changes; one added at runtime is the dock's copy (the app's object is left as it is). `title(id)`: its title now (`null` for none) |
| `onShown(fn)`, `onChange(fn)` | a panel came on screen (`fn(id)`); the layout changed (`fn(layout)`) |
| `onPopIn(fn)` | `fn(id)` just before a popped-out panel's element moves back from its window (see "The pop-out contract") |
| `onMenu(fn)` | `fn(id, itemId)` when an app item of a ⋯ menu that has no `run` of its own is picked, after the `onMenu` option; returns a function that takes `fn` off (see "App items in the ⋯ menu") (v0.9.0) |
| `narrow()`, `setNarrow(on)` | whether the dock is narrow; switch it (see "Narrow") |
| `can(id, action)` | whether a person may do `action` to a panel here: the `can` option, and narrow |
| `screenshot(id)` | `Promise<Blob \| null>`: draw (or capture, see `screenshotMode`) the visible panel frame as a PNG; `null` on cancellation, rejects on failure |
| `copyScreenshot(id)` | capture and copy the PNG, with a notice; resolves to the Blob (also when clipboard access fails), or `null` on cancellation/failure |
| `addPanel(panel, where?)` | add a panel `{ id, title, el, help?, icon?, closable?, unpinSize? }` at runtime, in front, saved, `onChange` told; `'added'`, or `'exists'` when the dock has that id (it is shown, as `reveal`, and nothing is added); `null` after `destroy()`. See "Panels at runtime" (v0.10.0) |
| `removePanel(id)` | take a panel out (any panel, closable or not, given to `createDock` or added): its window closes, its strip button, flyout or float goes, the tab on its left comes to the front; its place is kept for `addPanel`. Returns its element, detached and without the dock's classes, and fires `dock-removed` (`detail: { id, el }`) on the root; `null` for an id the dock does not have, or after `destroy()` (v0.10.0) |
| `close(id)`, `onClose(fn)` | close a closable panel as its × does: every `fn(id)` first (`false`, or a promise of `false`, keeps it), then `removePanel`; `Promise<boolean>`, whether it closed (v0.10.0) |
| `panels()`, `slots()`, `forget(id?)` | the dock's panels now; the ids whose places are kept for when they are added again (oldest first); drop one of those places, or all, returning how many went (v0.10.0) |
| `reset()`, `render()`, `destroy()` | the default layout; redraw; take the dock off the page |

Each panel's element also gets a `dock-shown` event when it comes on screen and a `dock-popin` event (`detail: { id,
window }`, it bubbles) just before it moves back from its own window, and a `dock-reveal` event dispatched from anything
inside a panel brings that panel on screen. The root gets `dock-removed` (`detail: { id, el }`) after `removePanel` or a
close.

Also exported: `panelsFrom(container)` (reads `data-panel`, `data-title`, `data-panel-help`), `mountPanelsMenu(dock,
button, menu, panels)` (`panels` a list, or a function such as `() => dock.panels()` for a dock whose panels come and go), `createHelp(content)`, `createTheme()`, `applyTheme()`, `THEME_LIST`, `mountThemePicker(theme,
button, menu)`, `isAppWindow()` and `mountInstallButton(button)` (`src/install.js`), the layout model (`src/layout.js`:
`makeConfig`, `normalizeLayout`, `stack`, `split`, `moveTo`, `floatPanel`, `popOutPanel`, `popInPanel`, …, all pure
functions over JSON), `oneColumn(layout, prefer)` (any layout as one stack of tabs), `POP_HTML` (the pop-out page as
text), and `src/host.js` for apps whose panels open dialogs (below).

## Options

| Option | Default | |
|---|---|---|
| `root` | required | the element the dock fills |
| `screenshot` | none | `async (id, el) => Blob \| null`: app capture hook; `el` is the whole panel frame in its current document, title bar included. Return an `image/png` Blob at the desired resolution, or `null` to cancel; errors become a failure notice when copying |
| `screenshotItem` | `true` | `false` hides Take Screenshot; the API remains available |
| `screenshotMode` | `'auto'` | `'auto'`: Dock draws the frame, no prompt, and asks to share the tab only for what it cannot read (a cross-origin iframe, a tainted canvas); `'draw'`: never asks; `'capture'`: always asks (Region Capture, as before v0.12.0). See “Panel screenshots” |
| `panels` | required | `[{ id, title, el, help?, icon?, unpinSize?, menuItems?, closable?, bodyAttrs? }]`; `icon` and `unpinSize`: see "The tool strip"; `menuItems`: see "App items in the ⋯ menu"; `closable: true`: see "Panels at runtime"; `bodyAttrs`: the option, for this panel's window. More can be added later (`addPanel`); `setTitle` changes a title |
| `menuItems` | none | `(id, ctx) => items`: the app's items in the ⋯ menu of a panel that has no `menuItems` of its own (see "App items in the ⋯ menu") |
| `onMenu` | none | `(id, itemId) => void`: an app item without a `run` of its own was picked (so do `onMenu(fn)`'s) |
| `keepSlots` | `50` | how many places of removed panels (and of panels a stored layout names that the app has not added yet) the layout keeps for `addPanel`; beyond it the oldest go (see "Panels at runtime") |
| `defaultLayout` | every panel side by side | a node from `stack()` / `split()`, `{ root, floats, auto, hidden }`, or `(ctx) => either` with `ctx = { viewportPx, purpose: 'load' \| 'reset' \| 'home' }` |
| `fill` | none | the panel whose place takes what is left in its split (the main view) |
| `minSize` | `{ w: 240, h: 90 }` | `{ id: { w, h } }` or `(id) => { w, h }` |
| `edgeOf` | `'right'` | `{ id: edge }` or `(id) => edge`: where a panel goes when nothing else says |
| `defaultSize` | an equal share | `(panelIds, dir, { viewportPx, extent }) => px \| null`: a splitter's double-click |
| `sizes` | `SIZES` | `head` 24, `bar` 5, `strip` 22, `edgeBand` 24, `keyStep` 16, `panelMin`, `floatMin`, `popMin`, `unpinSize` 420, `splitSize` 300 (px) |
| `storage`, `storageKey` (`key`) | `localStorage`, `'dock.layout'` | anything with `getItem` / `setItem` / `removeItem` |
| `migrate` | none | `(raw) => layout`: turns a saved layout of another version into this one |
| `narrow`, `narrowLayout`, `narrowKey` | `false`; every panel a tab of one stack, the `fill` panel in front; `storageKey + '.narrow'` | narrow (a phone): one column of tabs, nothing that moves a panel; its default layout; where its layout is kept (see "Narrow") |
| `can` | none | `(id, action) => boolean`, action `move float unpin pop max min hide close`: `false` takes that control, menu item (the Panels menu's too, for `hide`) and gesture away from a person (the app's own calls still work) |
| `minClickRestores` | `true` | a click on a minimised stack's title bar (a tab, or the bar beside the tabs; not its controls or help, not the click that ends a drag) restores it, as its restore control does, with the tab clicked in front; so do Enter and Space on its focused tab. A double click still maximises it (docked) or docks it back (floating), also when restoring moved the title bar from under the pointer. `false`: as before v0.3.5, only the restore control (and a double click) restores |
| `stripHover` | `true` | hovering a strip button for 250 ms slides its panel out, and the pointer leaving it slides it back; `false`: only a click (or the keys, or `reveal`) opens it, and the pointer leaving keeps it. Either way a click or focus elsewhere hides it (`stripAutoHide`) |
| `stripAutoHide` | `true` | a strip panel slid out (Dock Unpinned, Undock) slides back when a click or focus goes elsewhere in the page, as in IntelliJ, however it was opened; not while its ⋯ menu or a dialog is open, during a drag, when the whole window loses focus, or with focus in an iframe inside it. `false` (v0.5.0): one opened by a click with `stripHover: false` or beside stays until its button, its slide-in control, `closeFly()`, Esc or another strip panel |
| `stripReorder` | `true` | a strip button can be dragged along its strip to reorder it, or onto another edge (see "The tool strip"), and moved by **Alt+Shift+arrow**; `false`: neither (the app's `moveStrip` still works). `can(id, 'move')` false also keeps a panel's button still |
| `stripOpen` | `'over'` | where a strip panel slides out: `'over'` the layout; `'beside'` it, the middle narrowing to leave it its room (see "The tool strip"). A panel's own View Mode (Dock Unpinned: beside, Undock: over) overrides it, and is saved as its strip entry's `open` |
| `headButtons` | `'menu'` | a title bar's controls: `'menu'`, IntelliJ's ⋯ (Options) and − (Hide); `'classic'`, v0.4's buttons (menu, minimise, maximise, pop out, float or dock back, unpin; a flyout's pop out, float, slide in, pin) and its menu, exactly |
| `popUrl`, `popName`, `popTitle` | `'popout.html'`, `'dock-panel-'`, `(p) => p.title` | the pop-out window's page, window name prefix, and title |
| `popHtml` | none | `true`: open the pop-out page (`POP_HTML`) from a `blob:` URL of its text instead of loading `popUrl`; or the text of a page of the app's own (it needs an element with id `dk-pop-root`; its scripts run as any page's) |
| `popBase` | `document.baseURI` | with `popHtml`: the base URL of the pop-out page's relative URLs, put in it as `<base href>` (the page's own HTML `<base href>`, if it has one, is kept instead); the page is read with the browser's `DOMParser` and written back from it, so a comment before `<html>` is dropped; `false`: no `<base>`, the page as given, its base its `blob:` URL |
| `copyStyles` | `true` | copy the page's `<link rel=stylesheet>` and `<style>` into a pop-out window |
| `popBackButton` | `true` | the pop-out window's "Back to main window" button (`.dk-pop-back`); `false`: not rendered; its Options menu still changes View Mode |
| `outNoteDismiss` | `true` | the after-a-reload notice of panels out with no window has a × that closes it (the panels stay out: the Panels menu, `reveal(id)` or a strip panel's own strip button brings each back); `false`: no ×, the notice stays until each is reopened or brought back, for a dock with no Panels menu |
| `windowClose` | `'hide'` | closing a panel's window hides it and keeps Window mode; `'dock'` restores v0.5.x's return to its previous main-page place |
| `themeAttrs`, `themeEvent` | `data-theme data-scheme class style`, `'dock-theme'` | what of `<html>` a pop-out window copies, and the event that says the theme changed (a MutationObserver also watches) |
| `bodyAttrs` | `[]` | the names of the main page's `<body>` attributes copied to each panel window's `<body>` when it opens and kept in step (a MutationObserver, and `themeEvent`), as `themeAttrs` for `<html>`; one the main page takes off goes from the window too. `class` is merged: the window keeps its own classes (`dk-popwin`, and any its page put on) and drops only those it copied that the main page no longer has. A panel's own `bodyAttrs` (`panels: [{ …, bodyAttrs: ['class'] }]`, or `addPanel`'s) is used for its window instead; `[]` copies nothing for it. With `popUrl` and `popHtml` alike |
| `help` | none | `{ icon(key, panel) → html, mount(doc) → { destroy } \| fn, selector }`; `createHelp()` makes one |
| `modalSelector` | `[role="dialog"], .dk-help-pop` | clicks and Escape inside these leave unpinned panels and maximise alone |
| `badgeClass` | `'dk-badge'` | the class of a tab's badge |
| `text` | `TEXT` | any of the words the dock shows (`modes` and `modeHints`: the View Mode items and their tooltips) |
| `onReset` | none | called by `reset()` before the default layout comes back |
| `openWindow`, `win` | `window.open`, `window` | stand-ins for tests |

A saved layout is checked before use: repeats are dropped, a panel it names that the app has not given (or added) keeps
its place, unseen, for `addPanel` (v0.10.0; before, it was dropped), a panel it does not mention goes where the
default has it, and one that is not a layout, of another version (without `migrate`), names none of today's panels or
shows nothing gives the default.

`examples/opten-config.js` is opTen's configuration, the drop-in for opTen's adoption.

## Title bar and view modes

As in IntelliJ IDEA's new UI, a panel's title bar has its tab(s), **⋯** (Options) and **−** (Hide), and nothing else:

| Control | What it does |
|---|---|
| **⋯ › View Mode ▸** | **Dock Pinned** (docked in the layout), **Dock Unpinned** (on its strip, sliding out beside the middle, which narrows), **Undock** (on its strip, sliding out over the middle), **Float** (a window in the page), **Window** (a browser window of its own). Its own mode is checked; modes `can` or narrow forbid are left out |
| **⋯ › Move To ▸** | **Left**, **Right**, **Top**, **Bottom**: the side, in the mode it has. (IntelliJ has Left Top, Left Bottom, … : Dock's strip holds one group per edge, so its sides are its four edges) |
| **⋯ › Take Screenshot** | Copy the visible panel as a PNG, without a prompt by default (see “Panel screenshots”) |
| **⋯ › Maximise / Restore** | also a double click on the title bar or a tab (docked or floating), and Esc restores |
| **⋯ › Hide**, **−** | a slid-out panel slides back into its strip; a docked or floating one is minimised to its title bar (which shows its icon), and a click on it brings it back |

The menus work with the mouse (hover or click opens a submenu) and the keys (↑ ↓ Home End, → or Enter opens a submenu on
its checked item, ← or Esc closes it, Enter picks, Esc closes the menu and focus goes back to ⋯); they are `role="menu"`
with `menuitem` and `menuitemradio` (`aria-checked`) items, and stay inside the window near its edges.

The View Mode items say what they do (their tooltip, `text.modeHints`), as in IntelliJ:

| View Mode | Where | When you click elsewhere | Tooltip |
|---|---|---|---|
| Dock Pinned | docked, the other panels make room | stays | Docked; stays open |
| Dock Unpinned | on its edge's strip, sliding out beside the middle (which narrows) | hides | Docked on its edge; hides when you click elsewhere |
| Undock | on its edge's strip, sliding out over the middle | hides | Over the content; hides when you click elsewhere |
| Float | a free window in the page | stays | A free window in the page; stays open |
| Window | its own browser window | stays | Its own browser window |

"Hides" is a click, or focus (Tab, the app's own `focus()`), going elsewhere in the page: the panel slides back onto its
strip. Its ⋯ menu, a dialog it opened (`modalSelector`, or an open `<dialog>`), an iframe inside it, and the whole
browser window losing focus (alt-tab, a click in a panel's own window) keep it out. `stripAutoHide: false` gives
v0.5.0's behaviour (see the options).

**A panel's side is kept.** A strip panel pinned docks on its strip's side, beside the strip, as deep as it slid out
(or at its own saved size); a docked panel unpinned goes to the strip on the side it stands (the side is read from the
layout: where it is against the middle, the `fill` panel's place); Float and back, and Window and back, return it to its
side and place. Move To (or a drag) is the only thing that changes it, and every mode after uses the new side.

**A panel's size is kept per axis**, as IntelliJ keeps a tool window's. Move To from left or right to top or bottom (or
back) remembers how deep the panel was across the axis it leaves, and on the other axis it is as deep as it last was
there; the first time, its size in the default layout when it stands along that axis there, else `defaultSize`, else a
quarter of the dock (at least its `minSize`). Left to right keeps its width. A strip panel moved across axes does the
same with how far it slides out. The sizes are saved with the layout (`layout.depth`, `{ id: { w, h } }`); a layout
stored before has none, and loads as it is.

**Closing a panel's window** (OS close, Ctrl+W or `window.close()`) hides it and keeps Window mode. Its former strip
button, the Panels menu, `showPanel(id)`, `setVisible(id, true)` or `reveal(id)` reopens it in a window. Hidden windows
remain hidden after reload, without an out-notice. If reopening is blocked, the panel docks on its side with a notice. `windowClose: 'dock'` restores the previous close-to-dock behavior.

A panel's window has its tab, **⋯** and **−**, plus "Back to main window" unless `popBackButton: false`. Its Options
menu offers View Mode (Window checked), Move To and Hide; choosing another View Mode closes the window and shows
the panel in that mode on its side. Move To only changes its eventual side. Hide closes the window and hides the
panel. Menus belong to that window and close when it loses focus. Classic headers also have Float and Unpin
shortcuts; minimise, maximise and pop-out are omitted in a window. Back still restores its previous main-page mode,
with a strip panel slid out. Float again, or Window again, opens where it was last.

## The tool strip

An unpinned panel has a button on its edge's strip. Five options, each off unless asked for, make the strip a tool strip
(Ensemble's layout A: a list and a conversation fixed, the tools on the right edge):

```js
const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16v11H9l-5 4z"/></svg>';
createDock({
  root,
  panels: [
    { id: 'asks', title: 'Your asks', el: asksEl, icon: ICON, unpinSize: 360 },
    { id: 'changes', title: 'Changes', el: changesEl, icon: changesSvgElement, unpinSize: 760 },
    …
  ],
  stripHover: false,          // a click opens a tool; it stays open until closed
  stripOpen: 'beside',        // beside the middle, which narrows; not over it
  sizes: { strip: 44 },       // a wider strip: its buttons, icons and badges scale with it
  defaultLayout: { root: split('row', [stack(['list'], { size: 300 }), stack(['chat'])]),
    auto: [{ id: 'asks', edge: 'right' }, { id: 'changes', edge: 'right' }] },
});
dock.setBadge('asks', '3', '3 asks waiting');
```

- **`icon`** (a panel's, an SVG string or an element): its strip button shows the icon, with the title as its tooltip
  and `aria-label`. A string is put in as HTML (it is the app's own, not a person's); an element is copied, so the one
  given stays the app's. Without `icon` the button is the title as vertical text, as before.
- **Badges**: `setBadge(id, text, title)` also marks the panel's strip button: after the title on a text button, on the
  icon's top right corner on an icon button. It has the tab badge's class (`badgeClass`) and `dk-strip-badge`.
- **`stripHover: false`**: no 250 ms hover slide-out and no slide-in when the pointer leaves. A click on its button
  opens it; it goes back on a click or focus elsewhere in the page (v0.5.1; `stripAutoHide: false` keeps it through
  those), another click on its button, its slide-in control, Esc (focus goes back to its button), or another strip
  panel opening. The strip then lies above a panel sliding in or out,
  so a quick second click reaches the button.
- **`stripOpen: 'beside'`**: the panel slides out into room the layout gives it instead of over the layout: `.dk-main`
  gets a margin on that edge as wide as the panel is laid out (the app's CSS `max-width`/`max-height` on `.dk-flyout`
  counts, and the margin follows the panel's size as the window or the CSS changes it), the panel lies in it (between the middle and the strip, no shadow,
  no slide), and the splits are fitted again, so the middle narrows and the `fill` panel gives first. One panel is out
  at a time (so at most one per edge); opening another closes the first. It stays unpinned: nothing of it goes in the
  layout or in storage, and a reload shows the strip with nothing open (an app that wants a tool open again calls
  `openFly(id)`). A click or focus in the middle slides it back (v0.5.1; opened by a click, `stripAutoHide: false` keeps
  it); opened by hover (`stripHover` on) it also goes back when the pointer leaves, as over. The dock's root has `dk-beside-open` while a panel is out beside it.
- **`sizes.strip`**: the strip's buttons, icons and badges scale with it (CSS variables, each today's look at 22 px):
  the strip's padding and gap `strip / 11`, a title `max(--dk-fs-tab, strip × .3)`, an icon `strip × .55`, an icon's
  badge `max(7px, strip × .3)`, a title's badge `max(--dk-badge-fs, strip × .27)`. Each can be set (below).
- **Off the strip and back**: a strip panel floated (its flyout's float control) keeps its place on the strip: the
  float's Dock back control ("back to its strip"), a double click on its title bar, or Unpin puts it back on its strip,
  closed, where it was among its neighbours. So does popping it out and back, hiding and showing it, and pinning then
  unpinning it (with its original size and home). A minimised stack shows each panel's `icon` instead of its name.
- **Reorder by drag and drop** (v0.8.0, IntelliJ's stripe; `stripReorder: false` turns it off): press a strip button
  (icon or text) and drag it along its strip. From 5 px on the press is a drag: the panel slid out goes back, no hover
  slides one out, the button is dimmed, and a line in the strip shows where it will land; let go and the order changes.
  A press that moves less is still a click that opens or closes the panel. **Esc** cancels. Drag it onto another edge's
  strip, or to an edge without one, and the panel moves to that side in the view mode it has (Dock Unpinned stays Dock
  Unpinned), as Move To does. **Drop zones**: while dragging, every edge without a strip shows one for the drop (a
  dashed ghost as thick as a strip, over the layout); the strip or ghost the pointer is within `sizes.strip +
  sizes.edgeBand` px of is lit, with the line in it. Anywhere else the drop does nothing. The order and side are saved
  with the layout (the strip draws an edge's panels in `layout.auto`'s order), and every way off the strip and back
  (pinned, floated, in a window, minimised, hidden) returns the panel to its new place; its icon and badge move with
  it. By keys: with a strip button focused, **Alt+Shift+Up/Down** (left and right strips) or **Alt+Shift+Left/Right**
  (top and bottom) moves it one place; a screen reader hears where it is now ("Form: 2 of 4 on the right strip",
  `text.stripMoved`, given the side's name from `text.sides`) from a hidden `role="status"` region (`.dk-live`), and the
  button has `aria-keyshortcuts`. In the model: `moveStrip(layout, id, index, side)`. A hidden Window's strip button
  (v0.7.0) cannot be dragged, and a drop just before or just after it lands in the same place: its place on the strip
  follows the panel's, which is not on the strip while it is hidden.
- **`unpinSize`** (a panel's, px): how far it slides out of its strip, at least its `minSize`: when it is unpinned (by
  a person or `unpin()`, instead of its size where it was), and for a strip entry in `defaultLayout` without a
  `size`. A `size` on that entry, or one saved in the layout, wins. Once any panel gives one, a strip entry in
  `defaultLayout` without a `size`, of a panel without its own, gets `sizes.unpinSize` (420). Without it,
  `sizes.unpinSize` and the size where it was apply, as before. In the model: `makeConfig({ unpinSize: { id: px } | (id) => px })` and
  `cfg.unpinSizeOf(id, edge)`.

| Token | Default | Used by |
|---|---|---|
| `--dk-strip-pad` | `calc(var(--dk-strip) / 11)` | a strip's padding and the gap between its buttons |
| `--dk-strip-fs` | `max(var(--dk-fs-tab, 10px), calc(var(--dk-strip) * .3))` | a strip button's title |
| `--dk-strip-icon` | `calc(var(--dk-strip) * .55)` | a strip button's icon (width and height) |
| `--dk-strip-badge-fs` | a title's: `max(var(--dk-badge-fs, 9px), calc(var(--dk-strip) * .27))`; an icon's: `max(7px, calc(var(--dk-strip) * .3))` | a strip button's badge |
| `--dk-strip-badge-bg` | `var(--dk-bg3)` | an icon's badge's ground |

## Panel screenshots

**⋯ → Take Screenshot** (after Move To) captures the visible title bar and body, closes the menu first, and copies a
PNG. It works docked, floating, slid out, maximised and in a pop-out window. A notice says “Screenshot of &lt;title&gt; copied” or explains
the failure. The PNG has the frame's CSS width and height multiplied by `devicePixelRatio` (rounded to whole pixels).
Switching away from the panel or moving it to another frame during capture invalidates the screenshot, so another
tab's content cannot be copied under the original panel's name. Take the screenshot again after the change.

**No prompt (v0.12.0).** By default Dock draws the frame itself (`src/draw.js`): a copy of its DOM with every element's
computed style written in, inside an SVG `<foreignObject>`, painted on a canvas at `devicePixelRatio`. It keeps the
theme, `::before`/`::after`/placeholders, the web fonts in use (embedded), typed values, ticks and `<select>` choices,
textarea text, every scroll position, `<img>`, `<canvas>` and `<video>` pixels, inline SVG, CSS `url()` images and
same-origin iframes (drawn from their documents). It needs only standard APIs, so it also works where Region Capture
does not exist (Firefox, Safari). Compared with Chrome's own screenshot of the frame, at most ~2% of a frame's pixels
differ (`test/browser/screenshot-draw.js`). What a drawing does not show the same:

- text is anti-aliased in grey, not with the screen's coloured (LCD) subpixels;
- scrollbars are not drawn; their room is kept blank;
- shadow DOM content is not drawn; content of the page outside the frame that overlaps it (a popup over the panel) is
  not drawn either;
- a WebGL canvas made without `preserveDrawingBuffer` may come out empty; fonts loaded only with `new FontFace` (no
  `@font-face` rule) are not embedded;
- an `<input>` scrolled sideways shows its start; a horizontal scrollbar's room is not kept when the scrolling area
  has a bottom border; `::before`/`::after` of a scrolling area itself do not move with its scroll;
- form controls are drawn by the browser's image renderer; on Windows Chromium Dock gives checkboxes, radios, ranges and
  progress bars with no `accent-color` the system accent, as the screen shows them.

What drawing cannot read at all: a **cross-origin iframe** (another site's page), a **tainted canvas** (drawn from
another site's image) or an image (`<img>` or CSS `url()`) on another site that does not allow reading it (CORS). An
image the server does not have (an HTTP error) shows nothing on the page either and is drawn as nothing. Reading the
images and fonts must take under 3 s: asking to share the tab needs the click, which a browser honours for ~5 s, so
longer counts as "cannot read" in `'auto'` (`'draw'` waits). For those, `screenshotMode` decides:

| `screenshotMode` | Behaviour |
| --- | --- |
| `'auto'` (default) | draws; for what drawing cannot read, or if drawing fails, asks to share the tab (Region Capture, below). Without Region Capture it draws anyway, a cross-origin iframe's area filled with `--dk-bg` |
| `'draw'` | never asks: a cross-origin iframe's area is filled with `--dk-bg`, an unreadable canvas or image left empty |
| `'capture'` | always asks to share the tab, as before v0.12.0 |

**Region Capture** ([Region Capture](https://developer.chrome.com/docs/web-platform/region-capture), desktop Chrome
and Edge, on HTTPS or localhost) **asks you to share this tab**: choose the current tab, not another tab, a window or
the screen. Sharing stops after one captured frame, including on failure. Cancel closes sharing and says “Screenshot
cancelled”; browser denial of screen permission has the same result because browsers do not distinguish it from
cancelling the picker. Browser capture may resample pixels and slightly change colours. It captures cross-origin
iframes and overlapping content as seen on screen.

An app's `screenshot` hook takes precedence over all of this and controls whether a prompt is needed. With
`screenshotMode: 'capture'` and no hook, the item is hidden where Region Capture is missing.

The clipboard write reserves a Promise during the click. If permission or focus prevents copying, the captured PNG
stays available in a notice with **Copy** (a fresh click retries), **Download**, and **Dismiss**. Call the APIs from a
user gesture for browser capture/clipboard access. Hidden or inactive panels must be revealed first. With
`headButtons: 'classic'`, screenshots in the main page are API-only. Pop-out windows have Take Screenshot in their
Options menu (classic windows too), using that window's document, clipboard and device pixel ratio.

Translate through `text.screenshot`, `text.screenshotHint`, `text.screenshotCopied(title)`,
`text.screenshotCancelled`, `text.screenshotFailed(reason)`, `text.screenshotCopyFailed(reason)`,
`text.screenshotCopy`, `text.screenshotDownload`, and `text.screenshotDismiss`. The item's tooltip is
`text.screenshotHint` (“Copy this panel as a PNG”), or `text.screenshotHintCapture` (it adds “the browser asks to share
this tab”) with `screenshotMode: 'capture'` and no hook. Set `screenshotHint` to describe your app hook when it prompts.

## App items in the ⋯ menu

A panel's own actions (stop, resume, move to another project …) go in its ⋯ menu, **above Dock's own items**, with a
separator between, so there is one menu and not a second "more…" one (v0.9.0). Give a panel `menuItems`, or the dock
a `menuItems` option for every panel without its own:

```js
const dock = createDock({
  root, panels: [{ id: 'task', title: 'Task', el,
    menuItems: (id, ctx) => [
      { id: 'watch', label: 'Watch', checked: task.watched, run: () => task.toggleWatch() },
      task.running ? { id: 'stop', label: 'Stop', run: () => task.stop() } : { id: 'resume', label: 'Resume', run: () => task.resume() },
      { id: 'po', label: 'Make PO', disabled: !task.canLead, title: 'Only a running task can be PO' },
      { sep: true },
      { id: 'move', label: 'Move to project', sub: projects.map((p) => ({ id: `project:${p.id}`, label: p.name, checked: p.id === task.project })) },
    ] }],
  // Items without a run of their own come here (and to dock.onMenu(fn)'s).
  onMenu: (id, itemId) => { if (itemId.startsWith('project:')) moveTask(id, itemId.slice(8)); },
});
```

- **Asked on every open.** The hook is called each time the menu opens, so the items follow the panel's state; there
  is nothing to refresh. (A menu already open is not refreshed.) It is also called while a title bar is drawn for a
  panel that has nothing of Dock's in its menu, to know whether ⋯ shows at all (with classic heads, on each slide-out; in narrow mode, on each tab switch), so keep the
  hook cheap and free of side effects. Return `[]` (or nothing) for no items.
- **`ctx`**: `{ where, mode, side, window }`: `where` is `'dock'`, `'float'`, `'fly'` (slid out of its strip) or
  `'window'` (its own window); `mode` its view mode (`viewMode(id)`); `side` its side, or `null` for the middle;
  `window` the window the menu is in (the panel's own window when it is out).
- **An item**: `{ id, label, title?, disabled?, checked?, sub?, run? }`, or `{ sep: true }`. `label` and `title` are
  text (escaped, never HTML); `id` defaults to the label. `checked` (a boolean) makes it a checkbox item
  (`menuitemcheckbox`, ✓ when true). `title` is its tooltip; on a disabled item, say why. Separators are never first,
  last or doubled; anything that is not an item is left out.
- **Disabled** (`disabled: true`): shown, focusable and read out (`aria-disabled="true"`), with its title; a click,
  Enter or Space does nothing and the menu stays open.
- **A submenu** (`sub: [items]`): `Move to project ▸`, one level deep as Dock's own View Mode ▸ and Move To ▸ (an item
  inside one keeps no `sub`). The same pointer and keys: hover opens it, and with one open another item takes over
  after resting 150 ms; ↑ ↓ Home End move, Enter, Space or → open it on its checked item (else its first), ← or Esc
  close it, Tab closes the menu. An empty `sub` leaves its item disabled.
- **A pick**: the menu closes, then the item's `run(id, itemId)` is called; an item without `run` goes to the `onMenu`
  option and then to every `dock.onMenu(fn)` (which returns a function that takes `fn` off). It runs in the dock's
  page (where `createDock` was called), also when the menu was in the panel's own window. An exception (or a rejected
  promise) from the hook, `run` or `onMenu` is reported (`reportError`, else `console.error`) and the dock carries on: a
  hook that throws gives no app items that time.
- **Everywhere ⋯ is**: docked, floating, slid out of a strip (Dock Unpinned, Undock: working in the menu keeps the
  panel out), in its own window, narrow, and with `headButtons: 'classic'`, where they go above v0.4's menu items (a
  slid-out panel's title bar there gets a ⋯ for them alone). A panel with app items but nothing of Dock's in its menu
  (narrow, or `can` forbidding the rest) still gets ⋯.

`normalizeMenuItems(items)` and `menuItemsHtml(items)` (`src/menu-items.js`) are what the dock uses to clean and draw
them. The demo's Notes panel has Watch (checked), Stop / Resume, Archive (disabled, with its reason) and Move to
project ▸ Alpha, Beta, Gamma; each pick is a line in the Log.

## Panels stay in place (iframes do not reload)

A browser reloads every `<iframe>` in an element that leaves the document, even for a moment. So a layout change moves a
panel's element only when the element it is in really changes: the dock keeps its stacks' sections, its splits' boxes,
its floating windows and its slid-out panels from one drawing to the next, reuses each for the same layout node (or for
the one holding the panels it held), and puts children in order moving the fewest. Where the browser has
`Element.prototype.moveBefore` (Chrome and Edge 133+), the moves that remain keep the iframe's page too, and focus.

| A layout change | Panels whose element moves | Without `moveBefore` |
|---|---|---|
| a splitter dragged, double-clicked, moved with the keys | none | nothing reloads |
| a tab switched, a stack minimised or maximised | none | nothing reloads |
| another panel floated, docked back, unpinned, pinned, slid out, hidden, shown, popped out or back, when the split around this panel stays | none | nothing reloads |
| the split around a panel dissolves or appears (e.g. its only neighbour floats away, or a panel docks beside it) | its box moves up or down a level | its iframes reload |
| the panel itself floated, docked, unpinned, pinned, dragged into another stack | the panel | its iframes reload |
| the panel popped out, or back from its window | the panel, into another document | its iframes reload (always, `moveBefore` or not) |

A panel that is hidden or out waits in a hidden element of the dock (`.dk-parking`), not out of the document.

## Panels at runtime

A dock's panels are not fixed by `createDock`'s `panels` (v0.10.0): `addPanel` adds one, `removePanel` takes one out, and a
panel declared `closable: true` can be closed by a person, as IntelliJ's editor tabs (Ensemble opens each file it shows
as a panel of its own).

```js
const el = document.createElement('section');
dock.addPanel({ id: 'file:README.md', title: 'README.md', el, closable: true });          // a tab where you work
dock.addPanel({ id: 'file:a.js', title: 'a.js', el: el2, closable: true }, 'window');       // in its own window
dock.onClose((id) => (unsaved.has(id) ? askToClose(id) : true));                          // false keeps it
root.addEventListener('dock-removed', (e) => dispose(e.detail.id, e.detail.el));
```

- **Where it goes.** `where` is `{ kind: 'stack', stack: panelId }` (a tab of that panel's stack, docked or floating),
  `{ beside: panelId, side }` (docked beside it, as `moveTo`'s split; beside a floating one, a tab of it), an edge
  (`'left' | 'right' | 'top' | 'bottom'`, docked along it) or a view mode (`'pinned'`, `'unpinned'`, `'undock'`,
  `'float'`, `'window'`: placed as a tab, then switched as View Mode does, so `can()` decides; `'window'` opens its
  window at once, which a browser allows only from a click). With none it goes back where it was when it was removed
  (or before a reload, below); else it is a tab of the stack last worked in (a tab clicked, focus in it, the last one
  added), else of the middle (the `fill` panel's stack). A new tab goes right of the front one, as IntelliJ's. A
  `where` given wins over a kept place, which is then dropped. In narrow `where` is ignored (one column). A target
  that is not there counts as none. The panel comes to the front (a strip panel slides out), and the layout is saved.
- **An id the dock has**: `addPanel` shows that panel (`reveal`) and answers `'exists'`; nothing is added or moved.
- **Removing.** `removePanel(id)` takes any panel out: its own window closes (no note offers it again), its strip button
  and flyout, its float, its menu go; the next tab of its stack is **the one on its left** (the first tab's right
  neighbour when the first is removed), as IntelliJ's default; maximised alone, the dock is no longer maximised. Focus
  that was in it goes to that tab. The element is handed back, detached, with the dock's classes and the `role` and
  `aria-labelledby` it gave taken off; the dock forgets the panel (its badge, its window's place), and `dock-removed`
  fires on the root.
- **Closable panels.** `closable: true` gives the panel an **×** on its tab (seen on the front tab and the one under the
  pointer; not on a minimised stack's bar, whose tabs are icons: there a middle click or ⋯ → Close closes it), closing on a **middle click** on its tab, **Close** in its ⋯ menu (in its
  window's too, and in the classic menu) and **Ctrl+F4** with focus in it (see "Keys"). `can(id, 'close')` is true only
  for those (and the app's `can` may still say no). Closing asks every `onClose` hook first: one answering `false`, a
  promise of `false`, or throwing or rejecting keeps the panel (an app asks "unsaved changes?" there); with no promise
  among the answers it closes at once. Then it is `removePanel`. A panel that is not closable keeps the − (Hide), as
  before. `removePanel` asks no hook.
- **Its place is kept.** A removed panel's place (its stack and neighbours, its split side, its strip and place on it,
  its float's rectangle, its window's geometry, whether it was pinned from a strip) stays in the stored layout as
  `parked`, drawn nowhere and no tab. Stack-mates removed one after the other come back together, whichever is added
  first. After a reload, a stored layout that names a panel the app has not added yet loads without error and without a
  hole: the panel's place is kept the same way, and `addPanel` with no `where` puts it back there (one that was in its
  own window is offered again by the note, as after any reload: a window opens only from a click). Hidden when removed,
  it comes back shown, in the place it had. At most `keepSlots` places are kept (50 by default), the oldest dropped
  first; `slots()` lists them, `forget(id)` drops one, `forget()` all.
- **Many tabs.** Tabs keep their width (a title longer than `--dk-tab-max`, 160 px, is cut with an ellipsis; the tooltip
  has it whole). When a stack's tabs do not fit, its tab row scrolls sideways: the wheel over it, a thin scrollbar
  under it (drag it; dragging a tab moves the panel, as anywhere, so the row itself does not pan), and the keys (Left, Right, Home, End) scroll the tab they reach into view; the front tab is
  scrolled into view whenever it changes. A **▾** at the end of the row (there only while the row overflows, IntelliJ's
  Show Hidden Tabs) lists every tab of the stack, the front one checked and those out of view tagged *not in view*;
  picking one brings it to the front, into view, focused.

The demo's **new file** button adds File 1, File 2, … (closable; typed in, one asks before it closes), kept across a
reload in `dock-demo.files`; `?files=30` starts with 30 of them in one stack.

## Narrow

`narrow: true` (or `setNarrow(true)`, for example from a `matchMedia('(max-width: 640px)')` listener) makes the dock a
phone's: one column, every panel a tab of one stack. It draws no move (the per-panel menu), float, unpin, pop-out,
minimise or maximise control, starts no drag and no double-click maximise, and its API calls that would move a panel
(`float`, `unpin`, `moveTo`, `dockEdge`, `dockBack`, `toggleMin`, `toggleMax`, `popOut`) do nothing. Tabs still switch,
the Panels menu still hides and shows. Its layout is its own (`narrowLayout`, by default the wide default as one stack of
tabs with the `fill` panel in front), kept under `narrowKey`, so the wide layout is untouched and comes back as it was.
Going narrow closes pop-out windows; the wide layout keeps those panels as out, and its notice offers to open them again.
The dock has the class `dk-narrow` meanwhile: its tabs scroll sideways under a finger. The title bar keeps `sizes.head`
(24 px); an app that wants taller tabs on a phone makes them so in CSS under `.dk-narrow`.

`can(id, action)` is the finer tool: return `false` for `move`, `float`, `unpin`, `pop`, `max`, `min` or `hide` and that
control, its menu item and its gesture (`move`: a drag; `max`: a double-click; `float`: a double-click on a floating
window) go, for that panel; `hide` also disables its row in `mountPanelsMenu` while it is on screen. It governs what a
person can do, not the app's own calls.

## Keys

- **Tab** reaches a stack's front tab only; **Left** and **Right** move along its tabs (bringing each to the front),
  **Home** and **End** go to the first and last. The tabs are a `tablist` of `tab`s (`aria-selected`, a roving
  `tabindex`, an `id`), and a panel's element is their `tabpanel` (`aria-labelledby` its tab) unless the app gave it a
  role of its own. **Enter** or **Space** on a minimised stack's tab restores the stack with that tab in front
  (`minClickRestores`).
- **Alt+Shift+arrow** on a focused strip button moves it one place along its strip (Up/Down on the left and right
  strips, Left/Right on the top and bottom; `stripReorder`). No browser binds Alt+Shift+arrow (Back and Forward are
  Alt+Left/Right, without Shift), and a button has no text selection for it to extend.
- **F6** and **Shift+F6**, with focus anywhere in the dock, go to the next or previous stack's front tab: the docked
  stacks in order, then the floating windows, then a slid-out panel (only the maximised one while a stack is maximised).
  Outside the dock F6 stays the browser's. With focus inside an iframe in a panel (a text box in it, say) F6 works the
  same, if the iframe is **same-origin**: the dock puts its key listener on each same-origin iframe's document in the
  dock, again each time the iframe loads a page, and on iframes the app adds later; `destroy()` takes them off. A
  **cross-origin** iframe's document cannot be reached, so the dock skips it, silently, and F6 inside it stays the
  browser's (as do iframes nested inside a panel's iframe, and a panel's in its own window).
- **Ctrl+F4**, with focus in a closable panel (its tab, or anything in it), closes it (v0.10.0), as IntelliJ's editor.
  Chrome and Edge keep Ctrl+F4 in an ordinary browser tab (it closes the tab, and the page never sees the key), so
  there it works only in an installed app window or `--app` window, or in a panel's own window; the ×, a middle click on
  the tab and ⋯ → Close work everywhere. Firefox likewise closes the tab. Nothing else in the dock binds it.
- A **middle click** on a closable panel's tab closes it; its press starts no autoscroll.
- **Escape** restores a maximised stack and slides a slid-out panel back in; arrow keys on a focused splitter move it.

Every focus the dock gives is `{ preventScroll: true }`, and `.dock` and its boxes are `overflow: clip`, not `hidden`, so
none is a scroll container: focusing a panel that is still sliding in from the side cannot shift the dock sideways.

## Layers

The dock is a stacking context (`.dock { position: relative; z-index: 0 }`). Inside it: floating windows `20 + n` (n
their order, back to front), a slid-out panel 30, a maximised stack or window 40, the after-a-reload notice 55, the drag
preview 60. The per-panel menu and the Panels menu (30), help popovers (40) and the dock's short notes (100) are fixed
in the page's body. An app may set `z-index: auto` on `.dock` to put its own elements among these layers; they then
compete with the page's own z-indexes, so give an ancestor `isolation: isolate`. With panels staying in place (above),
an app no longer needs to lay an element over a panel's place to keep an iframe alive: put it in the panel.

## Theme

`css/theme.css` defines the colour tokens as custom properties on `:root`: light by default, and twenty sets in all, each
under `:root[data-theme="<name>"]`; with no `data-theme` the OS preference picks light or dark. `css/dock.css` uses only
these tokens, and an app's own styles can use them too, so the app and its panels look like one family.

**The sets** (`THEME_LIST` in `src/theme.js` names each as `{ name, label, scheme, origin }`: `scheme` is `'light'` or `'dark'`, `origin` the group, `'base'`, `'ensemble'` or `'extra'`):

| Group | Sets | Where from |
|---|---|---|
| base | `light`, `dark`, `dim`, `paper`, `contrast`, `fjord`, `tws` | opTen's seven themes (`stream-bridge/…/app/css/app.css`), colour for colour; `light` and `dark` were already the library's |
| ensemble | `ensemble-light`, `ensemble-dark`, `ensemble-dim`, `ensemble-paper`, `ensemble-contrast`, `ensemble-fjord` | Ensemble's six (`claude-dashboard-main/index.html`). They share names with opTen's but differ in shade (grounds, accent, quiet text), so they carry the `ensemble-` prefix and both looks are kept |
| extra | `solar-light`, `solar-dark`, `sepia`, `rose`, `dusk`, `forest`, `contrast-dark` | new: Solarized-like light and dark (text and accents deepened to pass), a warm sepia, a rose-and-plum light (Rosé Pine Dawn-like), a violet dusk (Dracula-like), a green forest dark, and a high-contrast dark |

Ensemble's tokens map onto the library's as: `--surface-overlay` → `--dk-bg` (menus, fields), `--surface-sunken` →
`--dk-bg2` (chrome), `--surface` → `--dk-bg3` (panel ground), `--border` → `--dk-line`, `--fg` / `--fg-subtle` /
`--fg-muted` → `--dk-fg` / `--dk-fg2` / `--dk-fg3`, the `-fg` of success / danger / warning → `--dk-ok` / `--dk-danger` /
`--dk-warn`, `--selected-bg` (worked out from its `color-mix`) → `--dk-sel-bg`.

**Legibility**, measured by `test/theme-sets.test.js` (WCAG 2 contrast): in every set, every text token (`fg`, `fg2`,
`fg3`, `accent`, `ok`, `danger`, `warn`) is at least 4.5:1 on `bg`, `bg2` and `bg3` (7:1 in the high-contrast sets), and
`warn` on `warn-bg`. The new sets also clear it on the hover and selected grounds. The same test checks the base and
Ensemble sets against the apps' own files, when those are on the machine.

**The picker**: `mountThemePicker(theme, button, menu)` lists "System" (light or dark, as the OS) and every set, grouped,
each with a swatch. A swatch is drawn in its own set's tokens: `theme.css` also applies each set to any element with
`data-dk-theme="<name>"`, so a preview is true whatever the page has on.

| Token | Meaning |
|---|---|
| `--dk-bg`, `--dk-bg2`, `--dk-bg3` | page ground; chrome (title bars, strips, splitters); panel ground |
| `--dk-fg`, `--dk-fg2`, `--dk-fg3` | text: main, secondary, quiet |
| `--dk-line`, `--dk-hover`, `--dk-sel-bg`, `--dk-focus` | borders; a hovered row; a selected item; the focus ring |
| `--dk-accent` | the active tab, a drop preview, a primary action |
| `--dk-ok`, `--dk-danger`, `--dk-warn`, `--dk-warn-bg` | good, bad, needs attention |
| `--dk-shadow`, `--dk-scrim` | floating things' shadow; behind a modal |
| `--dk-font`, `--dk-font-mono` | text; title bars, tabs, menus |
| `--dk-head`, `--dk-bar`, `--dk-strip` | layout: title bar height, splitter thickness, edge strip thickness (set from `sizes` on the dock's root and on a pop-out window's `#dk-pop-root`) |

**Chrome sizes**: the dock's type and shapes are tokens too, each optional. `dock.css` reads each with today's look as
its fallback, so set them (on `:root` or on the dock) instead of overriding class rules:

| Token | Default | Used by |
|---|---|---|
| `--dk-font-chrome` | `var(--dk-font-mono)` | title bars, tabs, strips, menus |
| `--dk-fs-tab` | `10px` | a tab's label, a strip button |
| `--dk-tab-case` | `uppercase` | tabs, strip buttons, a menu's section headings (`none` for sentence case) |
| `--dk-tab-tracking` | `.08em` (strips `.06em`) | their letter-spacing |
| `--dk-tab-weight` | `400` | a tab's weight |
| `--dk-fs-small` | `9.5px` | a menu's section heading |
| `--dk-fs-menu` | `11px` | menus, text buttons, the "every panel is elsewhere" text |
| `--dk-fs-note` | `11.5px` | the after-a-reload notice, short notes, help popovers, the pop-out window's waiting text |
| `--dk-badge-fs`, `--dk-badge-fg`, `--dk-badge-bg`, `--dk-badge-border`, `--dk-badge-pad`, `--dk-badge-radius`, `--dk-badge-weight`, `--dk-badge-case` | `9px`, `var(--dk-warn)`, `transparent`, `1px solid var(--dk-warn)`, `0 4px`, `0`, `400`, `none` | a tab's badge (for a quiet count: `--dk-badge-border: 0; --dk-badge-fg: var(--dk-fg3); --dk-badge-pad: 0`) |
| `--dk-radius` | `0` | floating windows, slid-out panels, menus, notices, help popovers |
| `--dk-float-shadow` | `0 10px 28px var(--dk-shadow)` | floating windows and slid-out panels |

```css
:root { --dk-fs-tab: 12px; --dk-tab-case: none; --dk-tab-tracking: 0; --dk-tab-weight: 600;
        --dk-badge-border: 0; --dk-badge-fg: var(--dk-fg3); --dk-badge-fs: 11px; --dk-fs-small: 11px; --dk-radius: 6px; }
```

**Switching**: set `data-theme` on `<html>`, or use the helper:

```js
import { createTheme } from './dock/src/theme.js';
const theme = createTheme({ key: 'myapp.theme' });  // remembered in storage; 'system' follows the OS
theme.toggle(); theme.set('dark'); theme.current(); theme.onChange(({ pref, theme }) => …);
```

`applyTheme(name)` sets `data-theme` and `data-scheme` (`light` or `dark`, for code that only cares which kind) and fires
`dock-theme` on the window. `createTheme()` knows every set in `THEME_LIST`; an app with other themes passes `themes: {
name: 'light' | 'dark' }`.

**Overriding**: redefine tokens after `theme.css`, for every theme or one:

```css
:root { --dk-accent: #7a3cff; }
:root[data-theme="dark"] { --dk-accent: #b89aff; }
:root[data-theme="sepia"] { /* every token */ }
```

An app with its own token names maps them instead of loading `theme.css`, as `examples/opten-theme-bridge.css` does for
opTen's seven themes. (Such an app's theme picker would pass its own `list`; the swatches need `theme.css` loaded.)

## The pop-out contract

A popped-out panel's element moves into a browser window of its own and keeps running in the main window: its timers,
data and listeners stay where they were, so it keeps updating. What the host app must provide:

1. **A pop-out page on the same origin** as the app's page, at `popUrl` (relative to the page). `src/popout.html` is
   that page; serve it (for example as `dock/src/popout.html`) or copy it. It needs only an element with id
   `dk-pop-root`; the dock copies the main page's stylesheets and theme attributes into it, and it closes itself when
   the main window closes or reloads. The page has one key for its windows (`window.__dockPopKey`), shared by every dock
   on it, so two docks on one page keep each other's windows open, and `destroy()` of one leaves the others'.
   **Or no page at all**: with `popHtml: true` the dock makes the same page (`POP_HTML`) a `Blob` and opens the
   window at its `blob:` URL, which is on the app's origin; `popHtml: '<!doctype html>…'` does the same with an app's
   own page. Nothing is fetched from the server, so a server that cannot serve `popout.html` does not matter. The
   browser loads it as a page: its doctype holds (standards mode), and its scripts run as any page's (inline, modules,
   `src`, `DOMContentLoaded`). Its relative URLs (links, images, iframes, scripts) resolve against the main page, as
   in `popout.html` beside it: the dock puts `<base href>` of the main page's `document.baseURI` first in its head
   (`popBase` sets another base, or `false` none, which leaves the `blob:` URL as the base, against which a relative URL
   finds nothing). An app's own page with a `<base href>` of its own keeps that one. The dock reads the page as the
   browser does (its `DOMParser`), so a `<base>` in a comment, a `<template>` or an svg does not count, and writes it
   back with its doctype (same mode); what lies outside `<html>`, such as a comment before it, is not kept. A reload of the window acts as with `popout.html`: the panel comes back to the
   main window. The dock keeps one URL (one for each base, if the page's base changes with `history.pushState`) and
   revokes them in `destroy()`. (Not measured: whether an installed app opens a
   `blob:` URL as an app window; see the next section for `popUrl`.)
2. **A click to open it.** Browsers open windows only from a user gesture, so `popOut(id)` must run from a click. If the
   browser blocks it, the dock says so and the panel stays.
3. **Panel code that does not assume the main document.** While out, the panel's elements are in the other window's
   document:
   - keep references to the panel's elements (or use `byId(id)` from `src/host.js`) rather than
     `document.getElementById` each time;
   - open dialogs in `el.ownerDocument` (or `hostDoc()`), so they appear in the window the person is using;
   - avoid `instanceof HTMLElement` and friends across windows.

**While it is out** the panel has no place in the main window's layout: if it was a tab, its tab goes; if it was alone in
its stack, the stack goes and its neighbours take the room. The layout remembers where it was (`out[id].was`, as a hidden
panel's). The Panels menu still lists it, marked "in its own window", with **Show window** and **Bring back**.

**Before it comes back** from its window (every way: the window closed, Bring back, Reset layout, going narrow,
`destroy()`), the panel's element gets a `dock-popin` event and every `onPopIn(fn)` is called with its id, while the
element is still in that window. That is the moment to take out what the app put in the panel for the window (an iframe
of its own, say), before it lands in the main page. It is not fired for a panel whose window never showed it.

**The "Back to main window" button** (or closing with `windowClose: 'dock'`) puts the panel back
where it was: the same stack at the same index, or the same side of the same neighbour (a whole split, if that is what
was there), its float, or its strip. If that place is gone it goes where the default layout has it, else on its edge.
**Reloading the main window** keeps the layout, with the panel out: a small notice over the dock (not a place in the
layout) offers "Open its window again" (one click, where it was) or "Bring it back here", and the Panels menu offers Open
window and Bring back. It does not reopen by itself, because a browser opens windows only on a user gesture. **Reset
layout** closes every pop-out window. A layout saved by an earlier version, with a panel out and still in its place,
loads with that panel taken out of its place.

## Pop-out windows without the address bar

A page cannot hide the address bar of a window it opens from an ordinary browser tab: Chrome and Edge show it on a popup
(the dock opens it with `popup=yes`; the browsers document no `window.open` feature that removes it, and this was not
tried feature by feature). They leave it out when the page itself runs as an app. Measured in Chrome 153 and Edge 153 on
Windows (`npm run evidence:app-window`, below), popping the demo's Counter out:

| The main page runs… | Pop-out window | Its frame (title bar, address bar) |
|---|---|---|
| in a browser tab | a popup with a read-only address bar | 74 px (Chrome), 72 px (Edge) |
| **installed as an app** (web manifest) | an app window: the app's icon and title, no address bar | 42 px |
| started with **`--app=<url>`** | an app window: a title bar, no address bar | 39 px |

For an app window the pop-out page must be inside the manifest's `scope` (the dock's `popout.html` is opened on the app's
origin; give the manifest a scope that holds both the app's page and `popUrl`, such as `"/"`). What the app needs:

1. **A web manifest**, linked from the app's page: `<link rel="manifest" href="manifest.webmanifest">`, served as
   `application/manifest+json`. `examples/manifest.webmanifest` is a template; `demo/manifest.webmanifest` is the demo's.
   It needs `name`, `start_url`, `scope`, `display: "standalone"` and 192 px and 512 px icons, and the page must be on
   HTTPS or `localhost`/`127.0.0.1`. No service worker is needed.
2. **A way to install it**: the Install icon in the address bar, or a button: `mountInstallButton(button)` (`src/install.js`)
   shows it while the browser offers to install and asks the browser to install on a click, and hides it once the page
   runs as an app or shows it again once the browser offers again — both without a reload, by listening for `change` on
   each display-mode media query and for `appinstalled`. It returns `{ destroy, canInstall() }`; `destroy()` removes all
   of its listeners, including these. `isAppWindow()` says whether the page runs as an app.
3. Or, with no install, **a shortcut** that starts the browser in app mode:
   `"C:\Program Files\Google\Chrome\Application\chrome.exe" --app=https://host/app/` (or `msedge.exe --app=…`).

Also tried, and not used by the dock: **Document Picture-in-Picture** (`documentPictureInPicture.requestWindow()`,
Chrome and Edge 116+). Measured: a window with no address bar (a strip with the origin, 42 px) from an ordinary tab.
Documented by the browsers, not measured here: only one per page, always on top, no control over where it opens, and it
closes when the page navigates; the dock's panels can each have a window of their own, so it stays with `window.open`.
Also documented, not measured: Firefox and Safari do not install web apps on Windows this way, so there the pop-out keeps
its address bar.

## Tests

```sh
npm test              # all of it
npm run test:node     # the model, the dock in jsdom with stand-in windows, the theme, the opTen fixture, test/needs.test.js
npm run test:browser  # headless Chrome (Puppeteer) against the demo: run.js (the pop-out window for real), run.js
                      # --pophtml (the same with popHtml, the page from a blob: URL), needs.js (iframes, narrow, focus, keys),
                      # toolstrip.js (the tool strip), scenarios.js, viewmodes.js (View Mode and Move To), autohide.js
                      # (Dock Unpinned and Undock hide when focus leaves them), stripreorder.js, runtime.js (panels added,
                      # removed and closed at runtime, and 40 tabs in one stack), menuitems.js (the app's items in ⋯),
                      # titlebody.js, screenshot.js (Take Screenshot with Region Capture and app hooks),
                      # screenshot-draw.js (drawn, no prompt: pixel checks against Chrome's screenshot, the fall-backs)
npm run test:browser:check     # one Chrome: says whether it made the blank-password check (see below); run it first
npm run screenshots -- <dir>   # the theme picker, the demo in ten themes, a panel out, its window, the reload notice
npm run evidence:app-window -- <dir>   # real Chrome and Edge (headed): the pop-out window in a tab, --app, installed, PiP
node test/browser/screenshot-demo.js <dir>   # headed demo menu → screenshot → Ctrl+V evidence (start npm run demo first)
node test/browser/screenshot-draw-demo.js <dir>   # headed: the drawn screenshot (no prompt), Form and Page, docked and in a window
```

Every Chrome these start goes through `test/browser/chrome.js`, on a persistent profile per suite under
`%LOCALAPPDATA%\dock-test-chrome\` (`run`, `run-pophtml`, `needs`, `toolstrip`, `runtime`, `viewmodes`, `autohide`, `menuitems`, `titlebody`, `screenshot`, `screenshot-draw`, `screenshot-draw-demo`, `screenshots`, `check`,
`app-window-<browser>-<way>`; `DOCK_TEST_CHROME` moves them; a run at the same time as another gets `<suite>-2`). Why:
Chrome on a new profile checks for a blank Windows password by signing in with an empty one, and Windows counts each
as a failed sign-in (10 in 10 minutes lock the account). Before each launch the helper seeds the profile's
`Local State` with that check's cached answer (`password_manager.os_password_last_changed` = now), so no launch makes
it. Each test runs in a fresh browser context, so the persistent profile keeps nothing between tests. Deleting the
folder is safe.

The browser tests prove, on the demo page, that a popped-out panel leaves no place behind and its neighbours take the
room, keeps receiving live updates, takes typing, clicks and its dialog, has the current theme and follows every set
picked in the theme picker, comes back through its menu or Back button (or closing with windowClose: dock), and
survives a reload of the main window (reopen, or bring it back, from the notice); all of it again with `popHtml`, where
`popout.html` is never requested, a relative URL in the window resolves against the main page, `popBase: false` leaves
the `blob:` URL as its base, and a page's own `<base>` is kept while one in a comment, svg or `<template>` is not. They
also prove that one click on a minimised stack's tab or empty title bar restores it (docked and floating, another tab
coming to the front), a control does only its own action, a drag or a move restores nothing, a double click ends
maximised (docked, also at the bottom where the title bar moves) or docked back (floating), Enter and Space restore
while the arrow keys still only switch tabs, and `minClickRestores: false` (the demo's `?minclick=false`) restores
nothing on a click. `needs.js` proves that the demo's iframe does not reload when splitters are dragged,
tabs switched, or other panels floated, unpinned, slid out or popped out, with and without `moveBefore`, and keeps its
page with it when its own panel moves; that a strip slide-out scrolls nothing (with `overflow: clip`, and with `hidden`
put back); the roving tabs, their roles and F6 (from inside an iframe panel too, after it reloads, and from one added later); narrow mode (no controls, no drag, no double-click maximise, its own
layout, a phone-sized window); and that `dock-popin` and `onPopIn` come while the panel is still in its window.
`toolstrip.js` proves the tool strip: without its options a text button in a 22 px strip, hover after 250 ms, over the
layout, as in v0.3.6; icons (tooltip, accessible name) and badges (icon and text) in the strip at 22 and 44 px, each
inside its button; click-only (no hover, stays when the pointer leaves, hides on a click elsewhere, and with
`stripAutoHide: false` stays through it and closes by its button, Esc, its control or another); beside (the middle narrows by exactly the panel's width, nothing covered, one at a time, nothing saved, a
reload shows the strip); a width per panel; and `?toolstrip=1` with all of them. `autohide.js` proves that Dock Unpinned and Undock hide on a
click or focus elsewhere, with hover on and off and in layout A, opened by a click, the keys, hover and `reveal`, their
strip button and stored layout unchanged; typing, their ⋯ menu, their dialog, an iframe in them and the window's blur
keep them; Esc closes them; Float and Dock Pinned stay; `stripAutoHide: false` is v0.5.0's; and the tooltips.
`menuitems.js` proves the app's ⋯ items docked, floating, slid out (Dock Unpinned, Undock), in a Window, narrow and
with classic title bars: above Dock's own with a separator, a pick runs once with its ids and closes the menu (the
strip panel staying out), `onMenu` for items without `run`, disabled items, the submenu by pointer and keys, the hook
asked on each open, a throwing `run`; and the demo's Notes path in the page and in its window.
