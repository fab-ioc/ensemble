# Dock

A small browser library for docked, tabbed, floating, unpinned and popped-out panels, with the theme and layout that go
with them. Plain ES modules, no build step, no runtime dependencies.

Shared by two apps:

- **opTen console** (`opTen-microservices/stream-bridge`), where it started as `app/js/dock.js`.
- **Ensemble** (`claude-dashboard-main`), whose PO screen will be rebuilt on it.

Each app picks up a tagged version when it chooses. Changes to the library are made here, not in the apps.

What a person can do with the panels: drag a tab to an edge of the dock or of another panel (split), into another panel
(a tab), float it in a window inside the page, unpin it to a strip on its edge (it slides out on hover or click),
minimise or maximise a stack, hide and show panels from a Panels menu, resize with the splitters (double-click one for
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
pops panels out without `popout.html` (`popHtml`). The demo is installable: installed, it and its pop-out windows
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
| `activate(id)`, `reveal(id)` | bring a panel to the front of its stack; `reveal` also shows a hidden one, slides out an unpinned one, focuses its window |
| `moveTo(id, target, side)`, `dockEdge(id, side)` | dock a panel beside a stack (`{ kind: 'stack', stack }`) or at the dock's edge; side `left right top bottom center` |
| `float(id)`, `dockBack(id)`, `unpin(id)`, `pin(id)` | float in the page and back; unpin to an edge strip and back |
| `toggleMin(id)`, `toggleMax(id)`, `restoreMax()`, `maximised()` | minimise / maximise its stack |
| `setVisible(id, on)` | hide or show a panel |
| `popOut(id)`, `popIn(id)`, `popWindow(id)`, `isOpenOut(id)` | a browser window of its own, and back where it was; its window; whether that window is open now (not after a reload) |
| `setBadge(id, text, title)` | a small badge on the panel's tab (`null` removes it) |
| `onShown(fn)`, `onChange(fn)` | a panel came on screen (`fn(id)`); the layout changed (`fn(layout)`) |
| `onPopIn(fn)` | `fn(id)` just before a popped-out panel's element moves back from its window (see "The pop-out contract") |
| `narrow()`, `setNarrow(on)` | whether the dock is narrow; switch it (see "Narrow") |
| `can(id, action)` | whether a person may do `action` to a panel here: the `can` option, and narrow |
| `reset()`, `render()`, `destroy()` | the default layout; redraw; take the dock off the page |

Each panel's element also gets a `dock-shown` event when it comes on screen and a `dock-popin` event (`detail: { id,
window }`, it bubbles) just before it moves back from its own window, and a `dock-reveal` event dispatched from anything
inside a panel brings that panel on screen.

Also exported: `panelsFrom(container)` (reads `data-panel`, `data-title`, `data-panel-help`), `mountPanelsMenu(dock,
button, menu, panels)`, `createHelp(content)`, `createTheme()`, `applyTheme()`, `THEME_LIST`, `mountThemePicker(theme,
button, menu)`, `isAppWindow()` and `mountInstallButton(button)` (`src/install.js`), the layout model (`src/layout.js`:
`makeConfig`, `normalizeLayout`, `stack`, `split`, `moveTo`, `floatPanel`, `popOutPanel`, `popInPanel`, …, all pure
functions over JSON), `oneColumn(layout, prefer)` (any layout as one stack of tabs), `POP_HTML` (the pop-out page as
text), and `src/host.js` for apps whose panels open dialogs (below).

## Options

| Option | Default | |
|---|---|---|
| `root` | required | the element the dock fills |
| `panels` | required | `[{ id, title, el, help? }]` |
| `defaultLayout` | every panel side by side | a node from `stack()` / `split()`, `{ root, floats, auto, hidden }`, or `(ctx) => either` with `ctx = { viewportPx, purpose: 'load' \| 'reset' \| 'home' }` |
| `fill` | none | the panel whose place takes what is left in its split (the main view) |
| `minSize` | `{ w: 240, h: 90 }` | `{ id: { w, h } }` or `(id) => { w, h }` |
| `edgeOf` | `'right'` | `{ id: edge }` or `(id) => edge`: where a panel goes when nothing else says |
| `defaultSize` | an equal share | `(panelIds, dir, { viewportPx, extent }) => px \| null`: a splitter's double-click |
| `sizes` | `SIZES` | `head` 24, `bar` 5, `strip` 22, `edgeBand` 24, `keyStep` 16, `panelMin`, `floatMin`, `popMin`, `unpinSize` 420, `splitSize` 300 (px) |
| `storage`, `storageKey` (`key`) | `localStorage`, `'dock.layout'` | anything with `getItem` / `setItem` / `removeItem` |
| `migrate` | none | `(raw) => layout`: turns a saved layout of another version into this one |
| `narrow`, `narrowLayout`, `narrowKey` | `false`; every panel a tab of one stack, the `fill` panel in front; `storageKey + '.narrow'` | narrow (a phone): one column of tabs, nothing that moves a panel; its default layout; where its layout is kept (see "Narrow") |
| `can` | none | `(id, action) => boolean`, action `move float unpin pop max min hide`: `false` takes that control, menu item (the Panels menu's too, for `hide`) and gesture away from a person (the app's own calls still work) |
| `popUrl`, `popName`, `popTitle` | `'popout.html'`, `'dock-panel-'`, `(p) => p.title` | the pop-out window's page, window name prefix, and title |
| `popHtml` | none | `true`: open the pop-out page (`POP_HTML`) from a `blob:` URL of its text instead of loading `popUrl`; or the text of a page of the app's own (it needs an element with id `dk-pop-root`; its scripts run as any page's) |
| `popBase` | `document.baseURI` | with `popHtml`: the base URL of the pop-out page's relative URLs, put in it as `<base href>` (the page's own HTML `<base href>`, if it has one, is kept instead); the page is read with the browser's `DOMParser` and written back from it, so a comment before `<html>` is dropped; `false`: no `<base>`, the page as given, its base its `blob:` URL |
| `copyStyles` | `true` | copy the page's `<link rel=stylesheet>` and `<style>` into a pop-out window |
| `themeAttrs`, `themeEvent` | `data-theme data-scheme class style`, `'dock-theme'` | what of `<html>` a pop-out window copies, and the event that says the theme changed (a MutationObserver also watches) |
| `help` | none | `{ icon(key, panel) → html, mount(doc) → { destroy } \| fn, selector }`; `createHelp()` makes one |
| `modalSelector` | `[role="dialog"], .dk-help-pop` | clicks and Escape inside these leave unpinned panels and maximise alone |
| `badgeClass` | `'dk-badge'` | the class of a tab's badge |
| `text` | `TEXT` | any of the words the dock shows |
| `onReset` | none | called by `reset()` before the default layout comes back |
| `openWindow`, `win` | `window.open`, `window` | stand-ins for tests |

A saved layout is checked before use: unknown panels and repeats are dropped, a panel it does not mention goes where the
default has it, and one that is not a layout, of another version (without `migrate`), names none of today's panels or
shows nothing gives the default.

`examples/opten-config.js` is opTen's configuration, the drop-in for opTen's adoption.

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
  role of its own.
- **F6** and **Shift+F6**, with focus anywhere in the dock, go to the next or previous stack's front tab: the docked
  stacks in order, then the floating windows, then a slid-out panel (only the maximised one while a stack is maximised).
  Outside the dock F6 stays the browser's. With focus inside an iframe in a panel (a text box in it, say) F6 works the
  same, if the iframe is **same-origin**: the dock puts its key listener on each same-origin iframe's document in the
  dock, again each time the iframe loads a page, and on iframes the app adds later; `destroy()` takes them off. A
  **cross-origin** iframe's document cannot be reached, so the dock skips it, silently, and F6 inside it stays the
  browser's (as do iframes nested inside a panel's iframe, and a panel's in its own window).
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
   the main window closes or reloads.
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

**Closing the window** (its close button, `window.close()`, or the "Back to main window" button) puts the panel back
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
                      # --pophtml (the same with popHtml, the page from a blob: URL), needs.js (iframes, narrow, focus, keys)
npm run screenshots -- <dir>   # the theme picker, the demo in ten themes, a panel out, its window, the reload notice
npm run evidence:app-window -- <dir>   # real Chrome and Edge (headed): the pop-out window in a tab, --app, installed, PiP
```

The browser tests prove, on the demo page, that a popped-out panel leaves no place behind and its neighbours take the
room, keeps receiving live updates, takes typing, clicks and its dialog, has the current theme and follows every set
picked in the theme picker, comes back to its stack when its window closes (three ways) or from the Panels menu, and
survives a reload of the main window (reopen, or bring it back, from the notice); all of it again with `popHtml`, where
`popout.html` is never requested, a relative URL in the window resolves against the main page, `popBase: false` leaves
the `blob:` URL as its base, and a page's own `<base>` is kept while one in a comment, svg or `<template>` is not. `needs.js` proves that the demo's iframe does not reload when splitters are dragged,
tabs switched, or other panels floated, unpinned, slid out or popped out, with and without `moveBefore`, and keeps its
page with it when its own panel moves; that a strip slide-out scrolls nothing (with `overflow: clip`, and with `hidden`
put back); the roving tabs, their roles and F6 (from inside an iframe panel too, after it reloads, and from one added later); narrow mode (no controls, no drag, no double-click maximise, its own
layout, a phone-sized window); and that `dock-popin` and `onPopIn` come while the panel is still in its window.
