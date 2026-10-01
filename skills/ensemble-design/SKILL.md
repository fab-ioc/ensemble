---
name: ensemble-design
description: The visual system and navigation rules for the Ensemble dashboard UI — colour tokens and what each one means, type/space/shape scale, component specs and their states, and the rules that keep new UI consistent. Read this before writing or changing any markup or CSS in index.html, session.html or fileview.html, before adding a colour, and before adding a control to the top bar.
---

# Ensemble design system

Every UI change in this project follows this document. It exists because the dashboard grew feature by
feature until one warm red was doing eight different jobs — primary action, focus, selection,
highlight, drag target, "live", "busy", "needs you" — and when every signal is the same colour, no
signal reads. The product owner's words were *"too much red… I struggle to navigate and identify
things."* Those were the same complaint twice.

The full reasoning lives in `design.md` in the task folder for
*Redesign the dashboard: Jira-like visuals and navigation*. **This file is the enforceable short form.
Where they disagree, `design.md` is right and this file gets fixed.**

The house style is Jira/Atlassian: a calm blue-grey ground, squarer than it is round, colour reserved
for meaning. No build step, no framework, one file per page, CDNs already in use only.

---

## 1. The one rule that matters most

There are **two kinds of colour** and they are never the same variable.

| Layer | Tokens | Themeable | Means |
|---|---|---|---|
| **Interaction** | `--accent`, `--accent-*`, `--selected-*`, `--link` | **Yes** — the user's accent picker | *What you can do.* Primary button, links, focus-adjacent borders, selected tab, selected row. |
| **Semantic** | `--c-*`, `--run-*`, `--prio-*`, `--agent-*`, `--match-*`, `--focus-ring` | **No** — fixed in meaning in every theme | *What is true.* Status, priority, health, identity, warnings. |

The user can set `--accent` to hot pink. **After that, every status colour must still mean what it
meant.** If a change you make would repaint a status, a priority arrow, a run dot or the focus ring
when the accent changes, the change is wrong.

`--focus-ring` is fixed and **not** user-overridable. It used to be `--accent`, which meant a pale
custom accent silently removed the visible focus indicator. Focus is not decoration.

### Where `--accent` is allowed

Primary buttons · links · the selected tab's underline · selected row / selected nav background
(via `--selected-bg` / `--selected-fg`) · focus-adjacent input borders · **the active drop target**.

**Nowhere else.** In particular, never for: hover states (use `--hover`), category headers, tree
selection, search highlights, chips that carry status, the user's own chat bubbles, or "live"
indicators. If you are reaching for the accent to say *"this one is special"*, you want `--selected-bg`
or a semantic token instead.

**The drop target is deliberately on the allowed list**, and it is the one case worth explaining
because the first draft of this file got it wrong. A drop target is not decoration saying "this is
highlighted" — it is a live affordance saying *"release here and it lands"*, which is squarely "what
you can do". What was wrong before was its **weight**, not its hue: a 12–14% accent fill across a whole
row shouts. The calm form is `--selected-bg` plus a 2px `--accent` inset, and it is the same in every
view — board column wells and list rows alike. One treatment, because a user dragging something should
not have to learn two.

---

## 2. Tokens

Copy from `index.html`'s `:root`. Never introduce a raw hex in a rule. If you need a colour that isn't
here, you are either misusing an existing meaning or the system needs a new token — raise it, don't
inline it.

### Theme — light unless the user chooses otherwise

**The dashboard is light by default. It does not follow the OS setting unless the user picks System.**
The product owner approved a light design, and for four slices the build silently went dark on every
machine set to dark mode — including his and both agents' — so nobody saw the approved design until
his first look, and it looked nothing like the mockup.

- **A theme is a complete token set.** Light lives on bare `:root`. Every other theme lives **once**,
  under `:root[data-theme="<name>"]`, and sets every colour token for its ground: neutrals,
  interaction, and the semantic tokens' shades. The themes are Light (default), Dark, Dim, Paper,
  High contrast (`contrast`) and Fjord, plus System, which resolves to Light or Dark.
- **A theme changes a semantic colour's shade, never its meaning.** Amber is "needs you" in every
  theme and red is "wrong" in every theme. Each theme's pairs are measured on that theme's ground.
- **Adding a theme:** add its block to all three pages with the same token names as the Dark block,
  add its name and ground (`light` or `dark`) to the `SCHEME` map in each page's head script, add it
  to the hub's allowed `theme` values in `dashboard.py` (the avatar menu's Theme submenu is built
  from `cdTheme.themes`, so it shows it by itself; give it a label and
  swatch colours in `THEME_INFO` in `index.html`), then measure every
  pair in §3 on it, including its `--code-*` set in `fileview.html` (see *Code* below). The ground
  picks which way the accent's hover mixes.
- **The choice follows the person, not the browser.** It is stored on the hub (`settings.theme`) so
  every device agrees. `localStorage['cd-theme']` is only the first-paint cache: the head script
  applies it before first paint, then fetches the hub's value and adopts it if it differs. If the
  hub has none yet, the page hands the hub its own, so a choice made before is kept.
- **Theme is a submenu of the avatar menu, never a flat run of rows.** One item, *Theme*, with the
  current theme's name and a `›` at its right; it opens a list of **every** theme plus Match system
  (`themeListHtml`, built from `cdTheme.themes`, the `SCHEME` keys the hub also allows). A row is a
  swatch (18px circle in the theme's ground, its accent as an 8px dot, ringed `--border`), the name,
  and a `✓` on the current one (`--selected-bg` row, `role="menuitemradio"`). On a desktop the list
  is a flyout to the left of the menu (the avatar is at the right edge); on a phone it is a nested
  list inside the menu, indented, never a flyout off screen. Click, or → on the item, opens it; ←
  and Esc fold it. Choosing a row applies it at once, saves it on the hub and closes the menu.
  **Accent** is the neighbouring submenu, with the same row renderer (`appearanceListHtml`),
  swatches, tick, keyboard behaviour and phone nesting. Its presets come from `ACCENT_CHOICES`;
  adding one there adds its row. Theme default restores the current theme's own accent; the
  custom colour field keeps opaque CSS colours available, and a custom choice gets a ticked row.
  Only one submenu opens at a time. Both choices live here, not in Settings.
- **A floating panel scrolls itself.** The page is a fixed-height shell, so a tray or panel dropped
  from the bar is `position: fixed` with a `max-height` of the room under its button and
  `overflow-y: auto`; it never relies on the page to scroll it into view (Settings lost its lower
  sections that way in #124).
- **Never key a colour off `@media (prefers-color-scheme)` directly.** It needs a second copy of a
  theme block, and a build that follows the OS by default is exactly how this one drifted from its
  mockup.
- `index.html`, `session.html` and `fileview.html` carry the same head script and listen for
  `storage` events, so the dashboard, the balloon and the file view never disagree.
- **The chat follows the theme.** A task folder's terminal colour scheme recolours its chat only
  when the owner switches it on for that task ("Chat in terminal colours" in the task's ⋯ menu,
  "🎨 Chat colours" in the balloon). It is off by default.
- Wrap storage access in `try/catch`. If the script fails or storage is blocked, no `data-theme` is
  set and `:root` gives light — the safe default.
- **Review in every theme, and the owner's first.** Four slices were built and reviewed only in dark
  because both agents' environments were dark-mode.

### Neutrals

```
--bg  --surface  --surface-sunken  --surface-overlay
--fg  --fg-subtle  --fg-muted  --fg-disabled
--border  --border-strong  --hover  --pressed
```

`--surface` always sits above `--bg`. Cards, rows, panels and the top bar are `--surface`; board
column wells and table heads are `--surface-sunken`.

### Semantic: six meanings, three tokens each

Every hue is `-bg` (lozenge wash) / `-fg` (text on that wash) / `-bold` (solid dot, bar, icon).
**Never invent a fourth variant.**

| Token stem | Means | Used for |
|---|---|---|
| `--c-neutral` | nothing in particular | Backlog, To do, Paused, "not started", counts |
| `--c-progress` | under way | In progress, "running" |
| `--c-success` | good / finished | Done, an agent working, clean repo |
| `--c-warning` | needs a human eventually | Waiting for you, Stalled, uncommitted changes |
| `--c-danger` | **wrong, or destroys data** | Blocked, Agent gone, delete/end controls |
| `--c-discovery` | set aside for judgement | In review, new |

**There is exactly one red.** `--c-danger` means something is wrong or this control destroys data.
Nothing else is red, ever.

**A state that is fine shows no colour.** Colour appears only when something needs someone. A meter
(the plan-usage bar is the model) is `--c-neutral-bold` while the allowance is fine, `--c-warning-bold`
when it warns, and `--c-danger-bold` only when a limit is actually reached. "Fine" is not
`--c-success` (nothing succeeded) and not `--c-progress` (consumption is not "under way").

**Uncertain is not wrong.** A stale or untrusted reading is `--c-warning`, never `--c-danger`, so red
stays unambiguous for the state that is actually broken. Mark it with a glyph, not a shade, and pick
the glyph that states what is known: the usage chip writes `≥ N%` for a reading that is a floor, `—`
for rolled over, `?` for reset unknown. "Approximately" (`~`) was proposed once and would have
misstated a floor.

### Run state — what a process is doing

`--run-working` (pulses) · `--run-idle` · `--run-off`. This is **separate from workflow status** and is
never a board column. Only `--run-working` pulses; nothing else in the product animates. Disable the
pulse under `prefers-reduced-motion`.

### Priority

`--prio-1` … `--prio-5`, highest to lowest. **Do not redraw the arrow icons and do not change the
hues** — they already match Jira's ramp. `--prio-3` (medium) keeps reduced opacity: the default should
not draw the eye.

### Agent identity

`--agent-claude-bg/-fg`, `--agent-codex-bg/-fg`. An avatar says **who**, never **what**, so these sit
outside the semantic six. They were briefly `--c-discovery` and `--c-progress` and produced an avatar
rendering the *identical* fill as the `In review` lozenge in the same row.

**Adding a third agent kind: its pair must clear 4.5:1 in every theme.** The avatar letter is 11px
bold, below the large-text threshold, so there is no exemption. Note that `claude` and `codex` both
begin with **C** — colour is load-bearing in that circle, not decorative.

### Search match

`--match-bg` / `--match-fg`. Fixed, for the same reason as the focus ring. A match must be *found by
eye on a quiet surface* — a neutral tint would be a few percent of contrast, and would vanish entirely
on a hovered row, which is exactly when the user is pointing at it.

### Code

`--code-kw` · `--code-str` · `--code-num` · `--code-fn` · `--code-ty` · `--code-attr` · `--code-meta` ·
`--code-tag` · `--code-com`. Syntax colours for code, highlighted by the product's own highlighter,
`static/hl.js` (the global `HL`): **no CDN**, because the hub is read over a tailnet that may have no
route out, and a highlighter that never arrived left every file grey. `fileview.html` (the Workspace
viewer) and `index.html` (the Changes tab's diffs) both load that one file; **never copy it into a
page**. Tokens, though, are per page: each page that highlights carries the `--code-*` set in every
theme block, with the same values, and its own `.tk-*` rules.

- **One hue per job, and no red.** Red means wrong, and a keyword is not wrong. A log's `ERROR` is, so
  it is `--c-danger-fg`; `WARNING` is `--c-warning-fg`. A diff row's ground is `--diff-add-bg` /
  `--diff-del-bg` (see *Diff* in §4), a hunk header `--c-progress-bg`; the code on them keeps its code
  colours.
- **Measured on every ground code sits on** (`--surface`, `--surface-sunken`, `--bg`): 5:1 or better on
  the light grounds, 6:1 on Dark and Dim (a colour at the bare minimum reads as dim there), 5:1 on
  Fjord, 7:1 in High contrast. Comments are `--fg-muted`, so they meet its floor.
- **High contrast is the owner's theme; judge code there first.** At 7:1 a green or teal reads as black,
  so its strings are blue and its hues are fully saturated.
- Line numbers are drawn (`::before` with `attr(data-n)`), never text: selecting, copying and the
  comment layer's offsets must see exactly the file.

### Layout

`--header-h` (49px: 48px bar + 1px border) · `--chrome-h` · `--sidebar-w` · `--detail-w` (a phone's
task, only) · `--list-w` (the task list on the left in layout A: `--sw-w` while `body.sw-on`, else 0; with the list in its dock,
#137, where the page starts, and `--list-r` / `--list-t` / `--list-b` the other sides, set by `ldInsets`) · `--conv-w`
(880px, the conversation's column in the middle; see *The middle* in §5).

**Never hard-code a header offset.** Nine literal `49px`/`56px`/`266px` values used to tie the sticky
table head, the sidebar, the detail panel, the notifications tray, the settings panel and the
Workspace and Changes tab heights to the bar's height. Use the token.

### Type, space, shape

- `--fs-100` 11/16 · `--fs-200` 12/16 · `--fs-300` **14/20, the body default** · `--fs-400` 16/24 ·
  `--fs-500` 20/24. Weights are **400 / 500 / 600 only**. **Nothing smaller than `--fs-100`**: a
  9px label arrived with a sibling feature and sat below the scale.
- **No `opacity` on text.** It turns the colour you measured into one you didn't: a muted colour at
  0.75 opacity no longer clears 4.5:1. Use `--fg-muted` directly.
- Spacing is a 4px grid: `--s-050 100 200 300 400 600 800`. Nothing between the steps — if a gap wants
  10px it is 8 or 12.
- `--r-100` 3px (lozenges) · `--r-200` 6px (buttons, inputs, cards) · `--r-300` 8px (panels, trays) ·
  `--r-full`.
- **Two elevations only**: `--e-100` resting card, `--e-200` overlay and dragging card. Docked panels
  use a border, not a shadow. No ad-hoc `box-shadow`.
- Tabular numerals for anything in a column. `--font-mono` at `--fs-200` for ids, paths, branches,
  models.

---

## 3. Measure, don't eyeball

**A screenshot is not evidence.** Every visual defect found in this redesign looked like "a bit off" in
a screenshot and only became a bug when the DOM was asked for numbers: a sticky column header painting
over the first card's title read as *missing titles*; a priority icon taking a whole row read as
*loose spacing*; an avatar failing AA by 0.12 was invisible entirely. Ask the DOM.

Two techniques that work here, both learned the hard way:

- **Responsive: use an `<iframe>`, not a window resize.** Resizing the browser window in this
  environment leaves the layout viewport untouched — `innerWidth` does not change — so media queries
  never fire and you measure the desktop rules at every width without knowing it. An iframe has its own
  viewport, so `matchMedia` inside it is real. Check the boundary from both sides (901 and 899).
- **Overflow: compare `scrollWidth` to the viewport**, and remember that content inside a horizontal
  scrollport (the board) is *supposed* to extend past it. A 1px difference is usually the scrollbar,
  not a defect.
- **`getComputedStyle` lies while anything is transitioning.** `main` and `#sidebar` both animate, so
  once you toggle classes to force a state you are reading the animation, not the cascade — a computed
  `padding-left: 0px` was observed while every matching rule said `248px`. When you need to know *which
  rule wins*, walk `document.styleSheets` and collect every rule where `el.matches(r.selectorText)`,
  reporting `{selector, value, media, matchMedia(media).matches}`. Match against the **whole**
  `selectorText` — `split(',')[0]` silently drops comma lists. And take baselines from a **fresh page
  load**; a page you have already poked has transitions in flight and every reading is fiction.
- **Check the cascade, not just the specificity.** A media block placed *above* the base rule it needs
  to override loses on source order at equal specificity. This shipped once as a drawer that got its
  overlay but not its scrim — an overlay you cannot dismiss by clicking beside it, which measured as a
  pass on every metric except the one nobody thought to check.
- **Check tokens by their computed value, never by their text.** The balloon and the file view once
  computed `--accent` as `a pale accent erased it */ --c-neutral-bg: #F1F2F4`: a script copying tokens
  between pages read the tail of a comment (`/* was --accent: a pale accent erased it */`) as a
  declaration. Every textual check passed. `--accent:` was "declared", the swallowed `--c-neutral-bg:`
  still appeared as text, and `--accent: #0C66E4` was present in the balloon even though a later line
  overrode it. Only `getComputedStyle(document.documentElement).getPropertyValue('--accent')` showed the
  garbage, and only painting a button showed the result: white text on a transparent background, in
  the default theme. To compare pages, read every token's computed value on each page in each theme and
  diff it against `index.html`. To check a control, paint it and read its colours. Any tool that reads
  a token block must strip comments and take the **last** declaration of each name.
- **Never write a custom-property name followed by a colon inside a comment.** Write "was the accent",
  not "was --accent:". It is a trap for every tool that reads CSS as text, including a plain grep.

### Contrast

**Every colour pair must be measured before it is written down.** "It looks fine" is how a 4.38:1
avatar got recorded as checked; it failed AA by 0.12, invisible by eye.

- Text and lozenges: **≥ 4.5:1**. All current pairs measure 4.53 – 14.10.
- `--fg-muted` is the **floor for anything a person reads** — worst case 4.53:1 on `--surface-sunken`.
- **`--fg-disabled` on `--surface-sunken` is 2.88:1 and clears no threshold of any kind** — not text of
  any size, not an icon, not a border. Use it only where WCAG 1.4.3 exempts it: a genuinely inactive
  control. Empty states, placeholders and hints use `--fg-muted`.

---

## 4. Components

### Button — four variants, no fifth

| Variant | Rest | Use |
|---|---|---|
| Primary | `--accent` fill | **One per surface.** Create, Start, Save. |
| Default | `--surface` + `--border-strong` | Everything else. |
| Subtle | transparent, `--fg-subtle` | Icon buttons, toolbar actions, close. |
| Danger | transparent, `--c-danger-fg`, `--c-danger-bg` on hover | Delete, End. **Never filled red at rest.** |

32px tall (compact 24px), `--r-200`, `--fs-300`/500.
Focus: `box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--focus-ring)`. Never `outline: none` alone.

### Lozenge — the workhorse

16px tall, `0 6px`, `--r-100`, `--fs-100`/600, uppercase, `letter-spacing: .05em`, `-bg` background
with `-fg` text, no border. **A lozenge never uses `--accent`.**

**Maximum two lozenges on a card or row.** If a third wants in, something else must go.

### Card

`--surface`, `--r-200`, `--e-100`, **no border**. Hover raises to `--e-200`. Title clamps at **2 lines,
never 3**. A 2px left rail in `--run-working` appears **only** while an agent is working — nothing else
gets a rail.

Order, top to bottom: attention lozenge (only when present, and it goes **first**, above the title,
because it is the reason you would look at the card) → title → priority arrow + run chip + cost +
project → agents with models + elapsed.

Cost is plain `--fs-100` `--fg-muted` text (`.ccost`), never a lozenge or a pill: dollars where a price
is known, else the tokens used (`1.4M tokens`, which is what a Codex task has), the split in its
tooltip; nothing while a task has used none.

The **change count** sits right after the cost in the same plain words (`.ccost.cchg`; on a list row
the row's `.row-cost-chip`): `+512 −4`, what a worktree task's branch has committed since it left
`main`, lines added and removed, the file count in its tooltip (`changesChipHtml` is the reference).
Nothing shows without a branch of its own, before the hub has counted it, or at `+0 −0` (merged, or
nothing committed yet). Never green and red: a size is not a verdict. The hub counts it per pair of
heads (the branch's, `main`'s) off the board's poll, so a new commit shows within about a minute.
From five digits up it reads in thousands (`+12.3k −123k`; the tooltip keeps the exact numbers).
The card's status line (`.crow1`) wraps rather than overflow: run, cost, count and points can
outgrow a 248px card.

### Unread dot

A task's chat, or a project's PO chat, has something newer than **this viewer** last read: an 8px
`--r-full` dot in `--c-discovery-bold` ("new"), never the accent, before the task's number on a card
and a list row, and after the project's name in the project menu (`unreadDotHtml`). It is a dot
with words: `aria-label="New messages"` and a tooltip saying since when.

- **The read point is the catch-up line's** (`cd-chat-read:<room>` in the browser, per viewer):
  reading a chat to its end, or Mark read, clears the dot in every open copy of the page at once.
- **What counts:** anything in the chat that is not the person's own and not a landmark (a rotation,
  a restart line); in a one-agent chat, the agent's last words. The hub sends it as `newsAt`.
- **Nothing before this browser first showed dots is unread**, so the first load lights nothing.
- It is not attention: `Needs you` says something waits on the person; the dot only says something
  happened. It never adds to a count.

Four signals must be legible **without hovering**: status, priority, assignees with their models, and
any attention state. Models are shortened on a card (`gpt-5.6-luna` → `luna`); the full string lives in
the issue view. Three or more agents show two, then `+1`.

### Avatar

20px, `--r-full`, **solid** fill from the agent tokens with light text. Solid fill is load-bearing, not
styling: it is what stops an avatar reading as a lozenge. Overlap −6px when stacked with a
`2px solid var(--surface)` ring.

### Tabs

A tab set is **fixed**: the same tabs, in the same order, with the same names, whatever state the
thing is in. The task panel is always `Activity · Changes · Workspace · Spec · Details`. What may change is the
pane behind a tab (a live session or a transcript under Activity; files, or "this task has no folder
yet", under Workspace) and which tab is selected by default. Never the set, the order or a label. A
tab that renames itself when a task starts is a quieter version of tabs that reorder.

**Each open file is a panel of the dock** (#150, the CEO's P105). Where the page has the dock (a
PO screen, an open task's: `pdPanelsOn()`), a file opened from a Workspace's tree is a closable
panel of its own (`file:<path>`, `wsPanelShow`), as IntelliJ's editor tabs are: its tab is the
file's name, its tooltip the path (`pdTabTips`), its ⋯ holds *Show in the tree* and *Open in new
window* above Dock's own. The first file opens beside the conversation, at its right, while the row has room for
one more (`pdFits`: the dock's width less the strip, against what the row's panels need at the
least, `pdNeedW`; else it is a tab of the conversation's stack, since a row that overflows is
clipped, not scrolled); the next ones are tabs of the file panel last shown (`wsPanelWhere`); a
file already open comes to the front, never opens twice; a line asked for (a diff, a search hit) reloads its viewer at that line.
The panel is the path bar (where the file is, Show in the tree, Follow, History for a documents
project, ⧉ its own tab) over the viewer (`.wfp`); a file that is gone shows the note where the
viewer was, with **Close**. The Files pane keeps the tree alone (`.ws-panels`: no tab row, no
viewer), except the roadmap, which is no file: its own view and editor still open in the pane, as
its one tab. A file closes as any closable panel (× on its tab, a middle click, ⋯ › Close, Ctrl+F4)
and its tab goes from the Workspace's model (`wsPanelRemoved`); the Workspaces that have panels
are remembered per browser (`cd-ws-panels`) and a reload puts each file back in its kept place
(`pdRuntimeRestore`: Dock parks a panel a stored layout names until it is added; a file gone
meanwhile closes quietly). One panel per file on the page: a Workspace that opens a file another
Workspace shows reveals that panel. A file's panel may be a tab of the conversation's stack: only
the tools are kept out of the middle (`pdKeepMiddle`). The tab model (`v.tabs`) stays the source
of truth, so Recent, Go to file and Text search open through it unchanged.

**Without the dock** (it could not load, or a page with none) the tabs are in the pane, as
before: each open file a tab above the viewer, as in an editor; these come and go as files are
opened and closed, so the fixed-set rule does not apply to them. Their rules:

- Opened from the tree, added at the end; a file already open is switched to, never opened twice.
  The row is never re-sorted.
- The one showing is `--surface` with the `--accent` underline; the rest sit on `--surface-sunken`
  in `--fg-muted`. A long name truncates; the full path is the tooltip **and** the breadcrumb under
  the row (see *Breadcrumb*), since a phone has no tooltip.
- Every tab shows its `×`; hover reveals nothing. A middle click also closes. By keyboard the row is
  one stop (roving `tabindex`): arrows, Home and End move, Enter or Space shows, Delete closes. Keys
  act only on a focused tab: no page-wide shortcuts.
- A tab keeps its place: its viewer stays loaded (hidden by `visibility`, never `display`, and never
  moved in the DOM), and reports its view, Wrap, marked lines and scroll so a reload restores them.
- A tab whose file has gone keeps its place and says so: struck-through name, "no longer exists" in
  its label, and a plain note with **Close tab** where the file was. Not red: nothing is broken.
- The row scrolls sideways within itself, never the page; on a phone each tab and its `×` are
  `--touch-min`.
- A Workspace (a project's or a task's) has no fixed tab: it opens on its tree and an empty pane.
  The roadmap's tab (see *Documents*) shows the roadmap's own view and editor where a viewer would
  be, and is never "no longer exists": not written yet, it offers to write it.
- **A file in a window of its own**: with the dock, the panel's View Mode › Window, also from the
  file row's menu (*Open in new window*, `wsRowMenu`) and the panel's ⋯; the window is Dock's,
  named after the file. Without the dock (#144, `wsOpenWindow`): the tab in front carries a monitor
  button before its `×` (`.wst-win`, the `×`'s box and colours; the dock's Window glyph, "Open in
  new window"), and Shift+Enter on a focused tab does the same. It opens the file view's page as a
  sized window without the browser's bars (960 × 820, no larger than the screen; each new one a
  step down and right of the window it is opened from), as the tab was left: view, Wrap, marked
  line, scroll. One window per file: asked for again, that window comes forward; other files get
  their own. A file row's menu offers it too: a right click on a file in any Workspace's tree
  (`wsRowMenu`: Open, Open in new window), and a documents project's row menu. Only the tab in
  front shows the button, so a row of tabs stays names and closes. **Not on a phone**: no button,
  no menu item; the path bar's link still opens a browser tab. A blocked window says so in a toast.

**A task in no project shows its files too** (#144). A task made without a project, a task brought
in from a terminal conversation and a past conversation (the Unassigned group) have one root: the
folder they run in, named **This task** or **This conversation** (`wsLoose`, `wsLooseFolder`), with
the same tree, viewer, Find and Changes. When there is nothing to show, one line says why (§5.5):
"No files in this task's folder yet.", "No files to show: this task's folder is no longer on the
hub.", "…has no folder.", or that it ran in a home folder or a whole drive, "too wide to show as its
own folder" (the hub's `folderWide`; it lists no such folder). A sentence in the tree
(`.wse.none`) wraps; it is never cut. Changes says the same way when the folder is only part of a
repository the hub does not read as a whole: "No changes to show: this folder is part of a larger
git checkout (name), which is not read from here." (`partOf` from `/api/git/roots`).

### Documents

The project's knowledge, in one place: the **first node of a project's Workspace tree**, above its
folders (`wsDocsNodeHtml` in `index.html` is the reference). It holds the roadmap, then the reports
and design notes its tasks put in its Documents folder and a code project's Markdown under `docs/`
in its checkout. There is no Documents panel and no Documents tab or list view: they split the
same thing in two (the CEO's P49). A task's Workspace has no such node.

- **A root heading like the others** (`.wse.wsroot.documents`, its kind word "documents"), **open by
  default**; opened or closed, it is remembered with the Workspace's tree.
- **The roadmap first**, in every state (loading, empty, listed): "Roadmap", when it was saved on
  the right (or "not written yet"), ROADMAP.md and the full time in its tooltip. It opens in a
  file tab like any document, and that tab shows the roadmap's own view and editor
  (`wsRoadmapPaint`, the one `#rm-panel`, parked by `rmPark` before a Workspace is rebuilt so a
  draft survives).
- **Then newest first**, by the file's time, in the order the hub sends. A row is a tree file row:
  the title (the file's name without `#12 ` and `.md`), the task's number as a quiet `.wse-tag`
  (`#12`, or `docs` for the code's), the date where a file's size would be; its folder, task, full
  time and size are its tooltip. One click opens it in a tab. No links inside a row.
- **Empty says what would fill it** (§5.5): where tasks put documents and how they are named. A
  capped list says where the rest are. Loading and unreadable are plain `.wse.none` rows.
- **Found like any file:** Go to file ranks the documents outside the folder it lists
  (`wsDocsFindPool`, named "Documents/…"), and Text search also searches the Documents folder when
  it is outside its root, merged with the documents first (`wsfMerge`). A query takes the tree's
  place as always.
- **Show in the tree** and Follow show a listed document on its row here, not in its folder.
- Read at most every 15 s while it is open or the find box is in use, one read at a time;
  rewritten only when it changes.
- **"Show task folders"** (one switch, remembered in the browser, shared with a documents project's
  Files panel): off, the tree leaves the tasks' folders out of the project's home. Go to file and
  Text search still look everywhere.
- An old link to a "documents" or "roadmap" project tab opens the Workspace.

The project **Changes** tab uses the same idea for code: uncommitted files on top only when there
are any, then **Landed on main**, a commit at a time (a merge counts as one), newest first: the
task's `.tno` link, the subject without `Merge #NN: `, when, then its files as the Changes rows
(the first 8, then "+N more"). A file opens that commit's diff, with line comments of its own.

### Changes: the files and the diff

A task's Changes (its branch against the line it left) and the project's are the same component
(#153, issue #4): the changed files on the left, the diff on the right, a splitter between them.
`tchMount` and `changesPanelHtml` in `index.html` are the reference.

- **Grouped by folder, by default.** The files are a tree (`chTree`, `chFilesHtml`): folders first,
  by name, then the files; a folder holding one folder and no file is one row (`static/dock/src`),
  never a chain of single rows. A folder row is `--fs-200`/600 with a `▾`/`▸` caret; a file row keeps
  its status lozenge and shows its **name**, the whole path in its tooltip. Each row ends in its
  lines, `+12 −3` (`.cnt`, `--fs-100` `--fg-muted`, tabular; the hub counts them per file with
  `git diff --numstat`; a binary file shows none); a folder's are its total. A level is a 14px step
  (`--d` on the row). In the project's Changes each group (Uncommitted, each commit's files) is its
  own tree, so a folder folded under one commit stays open under another. **Folders** (a small
  bordered button beside Refresh, pressed is `--selected-bg`) switches to the flat list, the whole
  paths in the hub's order, and back; the choice is remembered in the browser (`cd-ch-tree`).
  Folded folders are not remembered: a refresh opens the tree.
- **Keys:** one row is in the tab order (the file showing, else the first); ↑ ↓ Home End move along
  the rows showing, Enter or Space opens a file or folds a folder, → opens a folded folder, ← folds
  an open one. A folder folded or opened keeps the focus (`chListKey`, `chListFocus`).
- **Two panes and a splitter** (`chWireSplit`): the list's width is `--ch-w` on the grid, 300px by
  default, at least 180px and at most 60% of the panel. The splitter is a plain bar in Dock's look,
  not a dock inside the panel (that would be a second layout to save): 5px, `--surface-sunken` with
  a `--border` hairline, the accent while it is held, hovered or focused, `col-resize`; dragged with
  pointer capture, ← and → move it 16px, Home and End to the ends, a double click puts the default
  back. **One width for every Changes panel** (`cd-ch-w`), applied to each panel showing, a popped-out
  window's included. A `role="separator"` with `aria-valuenow`.
- **A narrow panel stacks them.** Under 680px of panel width (a tool open beside the conversation is
  about 500px) the files sit above the diff as before, with no splitter (`ch-narrow`, set from a
  ResizeObserver on the panel). On a phone the panel shows **one pane at a time**: the files, then
  the diff once a file is picked (`ch-show-diff`), with **‹ Files** at the start of the diff's bar
  (`.drv-back`, phone only) the way back; the pane not showing is `display: none` (no iframe here).


### Find in a Workspace

The box above a Workspace's tree (`.wsf` in `index.html`; `wsfMount` is the reference). It looks in the
Workspace's first root only: the task's folder in a task, the project's folder in a project.

- **Two modes, one box:** a `.seg` of **Files** (Go to file) and **Text** (Search in files). The
  placeholder says which, and where ("Go to file in this task"). Text adds two toggles, `Aa` (match
  case) and `.*` (regular expression): pressed is `--selected-bg`, as a selection. Files needs no
  options, so they are hidden there, not disabled.
- **Results take the tree's place** while there is a query, and the tree comes back when it is
  cleared. A text search widens the column to 40% for its lines.
- **Go to file:** the letters typed, in order, anywhere in the path (`wsptabs` finds
  `workspace_tabs.py`), best first: the file's own name, word starts and runs of letters count most.
  Each row is the name, then its folder in `--fg-muted`; the matched letters are `mark.match`
  (`--match-bg`). `name:120` opens at line 120.
- **Search in files:** a heading per file (name, folder, count), then its lines: line number in the
  gutter's mono, the line from a little before its first match, every match `mark.match`. Leading
  indentation is dropped; a cut line shows `…`.
- **The line under the box always says what the results are** and, per §5.5, why there are fewer than
  everything: "The first 1,000 matches, in 8 files; more results not shown", files skipped as binary or
  over 2 MB, a search stopped at its deadline, a query too short, a regex that is not one. It is
  `--fg-muted`, never red: a bad regex is not broken, just not yet a regex.
- **Keys act on the focused box only:** arrows move the selected result (`--selected-bg`, kept in view),
  Enter opens it (in Text before the search has started, it searches now), Escape clears the query.
  With a mouse a click opens a result and keeps the typing; on a touch screen it does not hold the
  keyboard up over the file.
- **A result opens through the one tab path** (`wsOpenTabAt`), at its line. A text match is also
  marked in the file (`mark.fv-hit` in `fileview.html`: `--match-bg` with a `--match-fg` underline, so it
  still stands out on the marked line's wash).
- **Typing again cancels** the search in flight, in the page and on the hub. The query, mode and options
  are remembered per Workspace with its tabs.
- **Phone:** the box is in the tree's pane, which is the whole Workspace while it shows (§8.9); the
  field is `--fs-400` and `--touch-min` tall, every toggle and result row `--touch-min`, and a matching line
  wraps instead of cutting its match off at the edge.

### Recent files

The files shown in a Workspace, most recent first, closed tabs included (`wsRecentToggle` in
`index.html` is the reference). It is a list, not a find, but it lives in the same box so there is
one place to go to a file.

- **A third button in the find box's `.seg`:** Files · Text · **Recent**. Pressed, the list takes the
  tree's place and the field filters it ("Filter recent files"); a Go to file or text query waits
  untouched until Recent closes. It is not remembered across a reload; the list is.
- **Rows are Go to file's rows** (`wsGotoHtml`): the name, then its folder under its root in
  `--fg-muted`, the root's name in front when it is not the Workspace's first root, matched letters
  `mark.match`. **A filter narrows, it never reorders**: most recent stays first.
- **It opens on the file shown before the one showing**, so the shortcut and Enter go back to it.
- **A recent file opens as it was left** (view, Wrap, marked line, scroll), through the one tab path
  (`wsOpenTabAt`): its tab if open, else a new tab with the state it last reported. Capped at 30,
  kept per Workspace with its tabs. A tab closed from "no longer exists" leaves the list.
- **Keys:** arrows and Enter as in find; Escape clears the filter, then closes Recent. The shortcut is
  **Alt+R (⌥R on a Mac)**, matched by `code` since ⌥R types ®, never with Ctrl (AltGr) or ⌘. It acts
  only while the Workspace pane has focus (the pane is `tabindex="-1"`, so a click anywhere in it
  counts) or its viewer does (`fv-key` from `fileview.html`). No page-wide shortcut. Ctrl/⌘+E was not
  used: browsers own it.
- The line under the box says how many, and "No recent file matches “x”" when a filter finds none.

### Breadcrumb

Where the file showing is: the bar under a Workspace's file tabs (`wsCrumbsHtml`).

- **Root name › folder › … › file**, in `--font-mono` at `--fs-200`; separators `›` in `--fg-muted`,
  not read aloud. The root and each folder is a Subtle button: a click shows that folder in the
  tree, opened. The file is plain `--fg`, weight 500, `aria-current`; its full path is its tooltip.
  A file in none of the Workspace's roots shows its whole path, with nothing to click.
- **Cut from the left:** a path too long keeps its end in view and scrolls sideways within itself (no
  scrollbar); a fade at the left edge (a mask, not a colour) says the start is cut.
- **Show in the tree** (target icon) opens the folders down to the file, marks it and scrolls the
  tree, and only the tree, to it. Marked is the tree's selection, `--selected-bg`: one row at a time,
  the revealed folder or else the file showing. From a click the find box clears to make way for the
  tree.
- **Follow** is a toggle (pressed is `--selected-bg`, as `.wsf-opt`): the tree follows the tab showing.
  It leaves a query alone and scrolls when the tree is back. Remembered per Workspace.
- Rewritten only when the path changes; a poll never replaces a crumb under the pointer.
- **Phone:** every crumb and button is `--touch-min`; the path swipes sideways within the bar.

### Diff

A changed file in a Changes tab (the task's and the project's) reads as in an IDE. The reference is
`drShow` in `index.html`.

- **Highlighted by `static/hl.js`**, each run of changed lines twice over: as the new file (context and
  added lines) and as the old (context and removed lines), so a string opened on a context line keeps
  its colour on the added line under it.
- **Grounds:** `--diff-add-bg` and `--diff-del-bg` are the success and danger washes mixed with
  `--surface` by `--diff-wash`, set per theme to the strongest mix at which every code colour and
  `--fg-muted` keep 4.5:1 (7:1 in High contrast): Light 90%, Dark 60%, High contrast 65%, Dim, Paper
  and Fjord 100%. A theme added later measures its own. A full-strength wash failed Dark (4.1:1).
- **Gutter:** old and new line numbers side by side, drawn from `data-o` / `data-n` by `::before` and
  `::after`, never text. Its width follows the largest number (`--gw`).
- **A sign before each changed line** (`+`, `−`), drawn by `::before`: colour is never the only signal,
  and a copy of the code carries no signs.
- **Long lines wrap** within the diff; the page never scrolls sideways. Rows are laid out in chunks of
  500 with `content-visibility: auto`, so a 5,000-line diff opens at once.
- The file bar says what the diff is (path, `+N −N`, how to comment) and offers **Open file**, which
  opens it in a Workspace tab at its first changed line. `diff --git`/`index`/`---`/`+++` lines are not
  shown: the bar says it.
- **Unified or side by side** (#153): a `.seg` in the bar, **Unified | Side by side**, pressed is
  `--selected-bg`; the choice is remembered in the browser (`cd-diff-mode`, unified by default). Side
  by side (`drPairs`, `.drv.split`) is a display row (`.sr`) per pair: an old cell and a new cell, each
  a `.dr` of its own side with one line number (`::before` on the old side, `::after` on the new), a
  `--border` hairline between the halves. A run of removed lines faces the added ones line for line;
  the extra ones face an empty `--surface-sunken` cell; a context line is on both sides; a hunk header
  spans both (`.sr.full`). Highlighting, the selection bar and line comments work on either side: a
  comment on a removed line's cell says "removed line N" and its card sits under the display row,
  lined up with the old side's code. A drag down one column selects that column alone (the press
  puts `sel-old` / `sel-new` on `.drv`, which makes the other side's text `user-select: none` until
  the next press), so the highlight and Copy never interleave the two sides. **A panel under 900px
  wide shows unified** whatever the choice
  and hides the switch (a ResizeObserver on the box; the choice is kept, and side by side returns with
  the width), so a phone and a tool beside the conversation never get two cramped columns. Rows are
  in chunks of 500 both ways: the largest merge of the week (5,784 lines) paints in under 100 ms in
  either mode.

### Line comments

Comments on a diff's lines reach the task's owner. They are the chat's review comments, not a second
mechanism: the same store (`static/comments.js`: one stored entry per comment, kept until the hub has
them), the same tray, the same `## Review comments (N)` message.

- **Start:** a click on a line number, or a selection across lines (followed through
  `selectionchange`). On a touch screen a tap anywhere on a line. **Range:** Shift-click, or on a touch
  screen a second tap, while a new comment is open. The lines under it take `--selected-bg` in the
  gutter and a 2px `--accent` inset: selection, as §1 allows.
- **The comment box opens under the last line**, not as a popover, with **Open at line N**, Cancel and
  Add comment. Ctrl/⌘+Enter adds, Escape cancels.
- **A comment shows under its line** as a card lined up with the code: `Not sent` (warning lozenge:
  it needs Submit) with Edit and Remove, or `Sent` (neutral lozenge) with when, and no controls. A diff
  that changed since keeps a comment under the same code nearby, else lists it above the diff under
  "On lines no longer in this diff". Nothing is lost silently.
- **The tray** is docked under the diff (`.cmt-tray`): count, Submit (primary), Copy, Clear, the unsent
  comments, and a line saying why Submit is off (a task that is not running) or why a send failed. A
  project's tray also picks the task chat. Submit sends one message, marks what went sent, and leaves a
  comment added or edited meanwhile unsent. A send carries a key made from the comments as they read
  (`drKey`), and the hub posts a key once, so two tabs or a retry never deliver the same comments
  twice; where the browser has a lock (https, localhost) a second tab also waits and sends nothing.
- **Phone:** every diff line is `--touch-min` tall, since a tap on it starts a comment (a line that
  wraps is taller anyway); comment cards span the width, every control is `--touch-min`, the comment
  field is `--fs-400`.

### Points

Every message the person sends an agent is a point (`P12`) the hub keeps until they acknowledge its
answer (`points.py`). The chat shows it; `session.html`'s `pointBarHtml` and `pointsLineHtml` are
the reference. It is information for the person, not an alarm: **no colour of its own, never in
Needs you.**

- **The person's name for them is "your asks"**: "Your asks" is the panel, the line and the
  summaries; the ids (`P12`), endpoints and `[point P12]` lines keep "point".
- **The line** (`#points-line`) sits under the ask line, in its look (`--surface`, `--border`,
  `--r-200`, `--fs-200`, `--fg-muted`): "Your asks: 2 waiting for an answer · 3 answered, not yet
  acknowledged". Hidden when both are 0. It is a disclosure button (`aria-expanded`, a `▸`/`▾`
  glyph) that opens a compact list under it, at most 40% of the height, scrolling within itself.
- **A list row:** the id in `--font-mono`, its first words (`--fg`, one line, cut with `…`, the
  whole text its tooltip), its age ("12m", patched in place, never a redraw), its state in words,
  "your message ↑" and "answer ↓" as links (`--link`), and its one click: **👍 Ack** for an answer,
  **Drop** for one still waiting. Answers to acknowledge first, then the waiting ones, oldest first.
  **The answer's own words show under the ask** (`.pt-said`, `--fg-muted`: the hub's `said`, the
  `Re Pn:` paragraph of the answer, else its first words, else an answer by doing's summary), so
  the ask reads with its answer without a jump (#146). A delivered ask has **Comment** beside its
  👍: it puts a new ask in the box, linked to the answer; sent, the hub reads the link and closes the
  ask as *followed up*. Nothing is posted by the click.
- **A follow-up closes an ask** (`points.py`, #146, issue #3): a comment on a passage of an answer,
  or a message linking or quoting one, makes the answered ask *followed up* (`followedBy`), the new
  ask its child (`replyTo`), down the chain. No thumbs up is owed; Ack and Reopen still work. The
  thread reads as quiet `.pt-thread` chips, "follow-up to P108 ↑" (linked to P108's answer) and
  "followed up by P109 ↓" (linked to the follow-up); a followed-up ask is closed and not listed,
  except as the root of a thread that still waits.
- **A link lands on the passage, not the balloon** (`gotoMsg(mid, part)`, `partOf`): "answer ↓" and
  "plan ↓" carry `re:P12` and mark the block starting "Re P12"; "your message ↑" carries `pt:P12`
  and marks the numbered item holding that ask's chip; a quote (`q:…`) marks its words. Only when
  the balloon has no such passage is the balloon itself marked. The mark is the landing mark
  (`.landed`: the drop-target treatment, fading), on the passage. A link from an ask carries `&part=`
  and can be copied as such; the balloon's own **Copy link** names the balloon. A comment on a
  *plan* balloon only links the thread: the ask stays in progress until its delivery.
- **The bar under a balloon** (`.pt-bar`, a hairline `--border` above it): on the person's balloon
  each point and where it stands ("P12 · waiting for an answer", "P12 · answered ↓" linked to the
  answer, "acknowledged", "dropped") with Drop or Reopen; on the agent's, "answers P12 ↑" linked to
  the person's balloon and 👍 Ack while it is not acknowledged. The one word that draws the eye is
  "answered", in `--fg` at weight 600: it waits for the person. On the person's balloon (`--hover`)
  the bar's words are `--fg-subtle` and its links `--fg` underlined: `--fg-muted` and `--link` there
  fall under 4.5:1 in Light.
- **Controls are Subtle buttons** (transparent, `--fg-subtle`, 24px, `--hover`, the focus ring),
  every one with its words in `aria-label`: an Ack types nothing into the agent, and says so in its
  tooltip. **👍 Go with it** sits only on a balloon that asks for a decision (or a task's own
  question) *and* holds a recommendation; it is the one control that types, once, and becomes
  "approved".
- **Never folded:** a balloon holding an open point or an unacknowledged answer is drawn in full
  whatever would fold it (age, its task's row, a Team activity gap), and the catch-up line names
  "N answers to your asks to acknowledge" first.
- **Team activity** (#146): over a transcript the hub types into, the chat shows the person only
  what is theirs (`forCeo`: their messages, the answers to them, a `Re Pn:` or `To <name>:` /
  `For you:` paragraph wherever it is, a decision, a message to them, a PO-to-PO message naming
  them). Every run of the rest, hub lines and the PO's notes on them alike, is **one row**
  (`.msg-fold.gap`, the hub row's look: `--surface-sunken`, a 1px left rule, no colour): "Team
  activity · 14 messages · 10:05–11:30 · 5 progress checks, 3 reports, 4 PO notes". A click opens
  it in place (its row stays above, `aria-expanded`), as a task's group row does; a balloon link
  into a closed gap opens it. The bar (`#chatbar`) says what is behind the lines ("… 86 team
  messages behind 8 lines") and its one button, **Team activity** (`#team-all`, pressed =
  `--selected-bg`), shows everything in place as before, per browser (`cd-chat-team`). It replaced
  "Just us", which still drew ~600 rows between the CEO's ~105 balloons over three days (measured in
  the #146 report). Never interleave hub rows between the person's balloons in the default view.
- **Outside the chat:** the task card (`.cpts`, beside the cost), the PO's header and its list row say
  "2 to acknowledge · 1 open" in `--fg-muted` words, the tooltip in full. No lozenge: a card keeps
  its two.
- **The Your asks panel** of a PO screen (see *Panels*; `pdPointsHtml` in `index.html` is the
  reference) is the same list with room of its own: the summary line in `--fg-subtle`, then a row a
  point: id and first words (two lines, `--fs-300`), then state, age, both links and the one click.
  The chat tells its host every change (`pointsTell`, with each point's words as the chat cuts
  them). While the panel is on screen the chat's own line steps aside (the frame carries
  `data-points-elsewhere`); hidden, the line comes back. An arrow brings the PO chat panel forward
  and lands on the balloon as the chat's own arrows do (`gotoMsg`); Ack and Drop post once and hand
  the hub's answer to the chat, so both show the same state at once. The panel's tab carries the
  count as quiet words (`.dk-badge`, `--fg-muted`), never a badge colour.
- **Phone:** the summary, every control and every link are `--touch-min`.

### Panels

**Layout A, step 3 (#125): the tool strip.** On a desktop the dock is the conversation in the
middle and a **44px strip at the right edge** holding the tools, in this order: **Your asks ·
Changes · Files · Board · Spec** (`PD_TOOLS`; Files is the panel `workspace`, Spec is `spec`: a
task's Spec with its Details under it). Each is an icon button (`PD_ICON`, 24-unit SVG, 1.8 stroke
in `currentColor`) whose name is its tooltip and `aria-label`; a count sits on the icon's top right
corner (`setBadge`: Your asks' open asks; Changes' uncommitted files on a PO screen, the branch's
lines added on a task, short: `+512`, `+1.7k`, the exact numbers in its tooltip). A click opens a
tool **beside** the conversation, which narrows (`stripOpen: 'beside'`: nothing is covered, no
shadow); a second click, its slide-in control or Esc puts it back; one is open at a time; hovering
opens nothing (`stripHover: false`). **A click or focus elsewhere in the page puts it back too**
(Dock v0.5.1, IntelliJ's Dock Unpinned and Undock), a click in the conversation included: the
chat's frame tells the page of a click (`chat-clicked`, `pdChatClicked`), since the dock hears no click inside
a frame. It stays out while it is used: typing in it, its ⋯ menu, a dialog or context menu it
opened (`modalSelector`), its own frames (the file view), Your asks' arrow, and when the browser
window loses focus. **A tool that should stay open is pinned** (⋯ › View Mode › Dock Pinned); a
Float stays too. Its width is its own (`pdToolSize`): Your asks 400, Spec 480,
Changes, Files and the Board 60% of what the list and the strip leave (480 to 760, the conversation
keeping at least 360). **The open tool is remembered per browser** (`cd-tool-open`) and opens
again after a reload; the middle changing conversation by code (not a click in the list) leaves
it out. A tool's title bar
(Dock v0.5.0, IntelliJ's; each View Mode item's tooltip says what it does) is its tab, **⋯** and **−**: ⋯ holds View Mode (Dock Pinned docks it
beside the conversation, Dock Unpinned, Undock, Float, Window pops it out), Move To (the side it
is on; only this changes a side), Maximise and Hide; − slides it back in. Closing a tool's window
hides it in Window mode (Dock v0.7.0); its former strip button or Panels reopens a window.
The window's own ⋯ offers View Mode to dock it back, Move To, Take Screenshot and Hide;
its − hides it too. Take Screenshot uses the browser's share-this-tab prompt where supported.
Split and maximise work as the library's. **The
conversation has the same title bar** (#148): its Chat tab, ⋯ with View Mode (Dock Pinned, Float,
Window: no strip for it), Take Screenshot, Maximise and Hide, and − (minimise); its window's ⋯
offers View Mode back, Take Screenshot and Hide. **It cannot be moved to a side** (`can` refuses
`move`: it is the dock's `fill`, and Move To would leave the middle empty with no way back but
Reset layout), and a tool dropped into its stack goes beside it (`pdKeepMiddle`). **Its window
holds the open task** (`pdPlaceTask` adopts `#detail-panel` into that document: the task's header,
action bar and chat, which reloads there as the library says of a popped-out panel) and is named
after it ("#12 The title · Project", `pdChatTitle`, kept in step by `pdPopTitle`); with no task
open it holds the PO's chat (`iframe.pd-own`). × in the window closes the task and the PO's chat
takes its place; a row clicked in the list puts the task there. Closing the window hides the panel
(the task waits, parked); its former place or Panels reopens the window. The page's body classes
are mirrored into every panel window (`onEveryWindow`), so rules keyed on `body.dp-docked` and
`body.mid` hold there. There is no ⧉ Pop out, no `session.html` window and no ⧉ Dock any more:
the panel's View Mode is the one way out and back. **The task's actions are in the panel's ⋯**
(#150, Dock v0.10.0's app items, need 14): the conversation panel's `menuItems` hook
(`pdMenuItems`) gives Dock the open task's action model (the same groups as a list row's,
`static/actions.js`), drawn above Dock's own items with the model's separators, wherever the menu
is: docked, floating, in the panel's own window, and on a phone's tab row (where it is the ⋯'s only
content besides Take Screenshot: `pdPlaceTask` redraws the row when the task comes or goes). A pick
runs the page's handler through a stand-in button with the item's class and data (`pdRunAction`);
Terminal colours opens its picker under the panel's ⋯, in that ⋯'s window. The bar under the task's
name keeps the primary alone (`actionBarHtml(model, { menu: false })`); its More ⋯ returns only
when the dock could not load. **As many chats as you like** (#150, the CEO's P110): a task's chat
opens as a closable panel of its own (`chat:<room>`, `pdChatOpen`; its tab "#12 The title",
`pdRowTitle`), beside the conversation at its right, then beside the chat panel last shown, while the row
has room for one more (`pdFits` again), else a tab of that panel's stack, from
*Open in new panel* (the action model's first group, offered where the page has the panels,
`env.panels`: in the middle's ⋯, where it moves the task out of the middle, so there is one chat
per task on the page; a plain click on a panelled task's row reveals its panel instead of taking
the middle), from a **Ctrl/⌘ click** or a **middle click** on a task's row (`pdRowPanel`; a
session with no room opens as a click does), and from a task link (`runTaskAction('panel')`). Its
⋯ holds the task's actions as the middle's does (`pdChatMenuItems`, without Open in new panel;
its terminal items act on its own frame); its chat is `session.html` embedded, polling its room
every 10 s while nobody types in it (`data-chat-slow`) and every 30 s while it is off screen
(behind a tab, parked, hidden: the host's classes on the frame's ancestors, `chatOffScreen`),
at once when it comes back or into focus; the middle's own chat polls every 2 s as before. The
rooms open are remembered per browser (`cd-chat-panels`) and come back in their kept places
after a reload (`pdRuntimeRestore`; a room no longer listed, or the task in the middle, is
dropped). File and chat panels are the runtime panels (`PD.rt`; `pdMinOf` their least size:
a chat the conversation's, a file 280 × 160); `dock-removed` disposes them (`pdRuntimeRemoved`).
On a phone both are tabs of the one column, as everything is. **Still interim**: the PO's two (Switch agent, the PO's task) wait in
its header's ⋯, marked `data-interim="dock-menu"`, until they follow the same hook. The Board's own **⤢** (`.pd-board-max`, beside its view
switch) takes the whole width and gives it back. **Panels is available on a desktop** (#143):
it recovers Tasks and tools whose closed window has no former strip button, including Spec.
The list's reload notice can be dismissed because Panels still offers recovery. The layout is kept under `cd-tool-strip` (the old `cd-po-dock` is not
read). An open task is the same dock's middle (see *The middle* in §5), with its own Changes,
Files and Spec in the tools. **A phone has the same tools as tabs** (#129): the dock is narrow
there (`pdNarrow()` is `isPhone()`), one column of tabs in the strip's order, `Chat · Your asks ·
Changes · Files · Board · Spec`, with Panels ▾ in the bar. An open task is that dock's Chat tab
too (`pdTask()` holds on a phone), so its tools are the same tabs, not the task panel's own:
tabs, not a sheet, because they are the same dock and panes as the desktop's strip (no new
component) and the mockup (`a2-…-390.png`) shows a tab row. The conversation's tab is **Chat**
(`PD_TITLES`), since it is a task's as often as the PO's; a popped-out window still says "PO chat".

A project with a PO opens on its **PO screen**: a Dock (`static/dock`, a vendored copy of the Dock
library; `VERSION` names its commit) of five panels, **PO chat, Your asks, Board, Workspace,
Changes** (`PD_IDS` in `index.html`; Your asks is `points`). The person arranges them: side by side, as tabs of one stack, floating,
on a strip at an edge (slides out on hover or click), minimised, maximised, hidden from the Panels
menu, or popped out into a window of their own. **One click on a minimised panel's title bar
brings it back** (the library's, from v0.3.5: `minClickRestores`, on by default); its controls
keep their own clicks, a drag is not a click, and a double click still maximises (also when the
restored chat's iframe slides under the pointer: for 500 ms after that click the dock's iframes
let clicks through, `pdDblGuard`). The layout is remembered in the browser (`cd-po-dock`; a phone's apart, `cd-phone-tabs`: a new key
since #129, as the older one had Spec hidden), with **Reset layout** in the Panels menu. The
library is never edited in this repository: a need goes to the Dock project's `ENSEMBLE-NEEDS.md`.
The page has two docks (this one and the task list's, #137): Dock v0.9.0+ keeps one pop-out key
per page, so both docks' windows find their way back (the page's own `pdKeepPopKey` went with
#150; ENSEMBLE-NEEDS item 9 is met).

- **The default** (`pdDefaultLayout`, measured in `tests/test_po_dock.py` and
  `tests/test_tool_strip.py`): on a desktop, at every width, the conversation alone and every tool
  on the strip, none open. A layout saved with a panel that is gone (the Documents panel) loads
  without it: Dock v0.10.0 parks a panel it does not know, so `pdEnsure` tells it to `forget`
  that one. A phone (`MOBILE_MQ`) is one column:
  every panel a tab of one stack, the Chat first and Spec last; nothing floats, sits on a strip or
  pops out there, and a tab is not dragged.
- **Icon:** the top bar's mark is the wordmark, or where it does not fit the app's icon
  (`/static/icons/favicon.svg`, 22px; see *The wordmark* in §5.1), and a popped-out panel's window
  takes the icon as its own (`pdPopIcon`).
- **Tokens:** the library's `--dk-*` tokens read Ensemble's own (`--dk-bg` `--bg`, `--dk-bg2`
  `--surface-sunken`, `--dk-bg3` `--surface`, `--dk-line` `--border`, `--dk-accent` `--accent`,
  `--dk-focus` `--focus-ring`, …) in `:root`, so every theme and the person's accent apply without a
  theme block; its `css/theme.css` is not loaded. Its size tokens put its chrome's type and shapes on
  Ensemble's scale (`--dk-font-chrome`, `--dk-fs-tab`, `--dk-tab-case`, `--dk-badge-*`, `--dk-radius`,
  `--dk-float-shadow`, …); its stylesheet is loaded first and Ensemble's rules after it do the rest
  (button sizes, hover and selected grounds, the focus ring).
- **Chrome:** a title bar is 32px on `--surface-sunken` with a `--border` under it. A tab is
  `--fs-200` at 600 in `--fg-subtle`, sentence case; the one in front is `--fg` on `--surface` with
  the `--accent` underline (§1's selected tab). The bar's controls (**⋯** and **−**, the library's
  default, not `headButtons: 'classic'`) are Subtle buttons, 24px, `--hover`; ⋯'s menu and its
  submenus are the library's `.dk-menu`s, styled as every menu below. A splitter is 5px of `--bg`,
  `--border-strong` on hover, `--accent` while dragged or focused (a control you are using). An edge
  strip is `--surface-sunken`; the tool strip's buttons are square icon buttons (Default: `--surface`,
  `--border`, the icon `--fg-subtle`, 55% of the strip), their count 10px 600 `--fg` on `--surface`
  in the icon's corner; the one open is `--selected-bg` with `--selected-fg`. A text strip button
  (a panel without an icon) is its name, `--fs-200`, 500. A floating window and a slid-out strip panel are `--r-300` with
  `--e-200`; docked panels have a border, no shadow (§2). The drop preview is the drop target of §1.
  Menus are `--surface-overlay`, `--r-300`, `--e-200`, rows 32px.
- **Panels ▾ is in the top bar on a phone** (`#bar-here`, §5.1; a Default button, `pdCtlHtml`;
  not on a desktop in layout A): each panel
  with its check, then Reset layout. Nothing sits between the bar and the dock. No tab row: the panels are the
  tabs. A tab asked for from elsewhere (a diff's Open file, an old "workspace" tab) brings its panel
  forward instead.
- **Panel code never looks its elements up by id in the main document** (`PD.els`, or `pdById`,
  which is the library's `byId`), and asks an element for its own document and window
  (`el.ownerDocument`: focus, a selection, a menu's room): a popped-out panel's elements are in its
  window's document. A popped-out window gets the page's stylesheets, its theme attributes, its
  document-wide listeners (`DOC_LISTENERS`, the library's own left out) and its frames' messages,
  so a click, a key or a selection there works as here. **What opens over the page opens where the
  person is working** (`pdHostDoc`, the library's `hostDoc`): the page's dialog (`pdModal()`, never
  `$('#modal')` to open one), a toast, a menu under its button, a selection's Comment button; `$`
  finds an element in a popped-out window when this page has none.
- **The PO's header** (`poHeadHtml`, #126, the mockup's conversation header): its avatar,
  "*Project* · PO" at `--fs-400` 600, and one `--fs-200` `--fg-muted` line of what is true: agent
  and model, the project, its open tasks (`poOpenCount`), the run chip, your asks. **Resume** is
  the one button (Default), and only while the PO is not running; Switch agent and the PO's task
  wait in a `⋯` menu (`.po-menu`, `--surface-overlay`, `--e-200`; `poMenuItems` is the data), whose
  items keep their `data-po`. Esc or a click elsewhere closes it; the header is not rewritten while
  it is open. A drawer keeps its `×`. Pop out is the panel's own (⋯ › View Mode › Window, #148);
  this `⋯` is interim (`data-interim="dock-menu"`): its items can go into the panel's ⋯ the way
  the task's did in #150 (`pdMenuItems`, Dock's `menuItems` hook); a small follow-up.
- **The PO chat** is in its panel: `#po-panel` moves into the PO chat panel on a PO screen and back
  home (the drawer) anywhere else (`pdPlaceChat`, with `moveBefore`, so its iframe keeps its page;
  the library moves panels the same way, so a layout change reloads no chat and no open file).
  While the panel is hidden or popped out it waits at home, hidden with `visibility`, never
  `display`. Popped out, the panel's window holds a chat of its own (removed in `onPopIn`, before the
  panel comes back); the one here stays loaded.
- **A popped-out window has no "Back to main window"** (the CEO's P50): `popBackButton: false`
  leaves the button out entirely; no Ensemble CSS needed. Closing hides the panel in Window mode
  (Dock v0.7.0). The window's ⋯ → View Mode docks it back; Panels or its former strip button
  reopens a hidden window.
- **The roadmap** is a document: the first row of the Workspace's Documents node, opening in a tab
  that is its own view and editor. There is no Roadmap tab or panel.
- **Phone:** the same dock, narrow (the library's `narrow`, switched by `setNarrow` on resize; its
  layout kept apart, under `cd-phone-tabs`): one column of tabs, no control that moves a panel.
  The tab row is `--touch-min` tall (plus its 1px rule) and scrolls sideways; the dock fills the
  height under the chrome (`--vv-h`). An open task's tabs sit straight under the bar (no gap, no
  border: `body.dp-docked main`), and its own `×` is gone (the bar's `←` closes it).
- **Keys** (the library's): F6 / Shift+F6 go between the stacks, the arrow keys along a stack's tabs;
  Tab reaches a stack's front tab only.

### Message editor

The chat's box (`#compose` in `session.html`; `edSerialize` and `pointItems` are the reference) is a
small editor: the words at the top are the message, as ever, and under them **numbered points**, each
a block with its own text, its own images and its own balloon links. It replaced the `To` drop-down:
a message is directed with its `@codex` / `@claude` prefix, which the placeholder names.

- **A point is a block:** its number (`--fg-muted`, `--fs-200`, 600) beside a box that grows with its
  words, its image chips (`.att-chips`, as the head's) and its balloon links as `ref-chip`s under the
  box, and Move up · Move down · Remove as Subtle buttons (24px, `--fg-subtle`, words in `aria-label`).
  The block is `--surface` with a `--border` that turns `--border-strong` while a box in it has focus.
  Nothing in the editor is a colour of its own.
- **Keys:** Ctrl/⌘+Enter sends; Ctrl/⌘+Shift+Enter adds a point; Enter in a point is a new line in
  it; Tab in the last point adds one once it has words (otherwise Tab moves focus, as a keyboard user
  expects); Backspace in an empty point removes it; `1. ` or `- ` typed at the very start of the head
  turns its words into the first point. The placeholders say so, so they are `--fg-muted`, never
  the browser's default grey. The head's is short (`composePlaceholder`): who it goes to, how to
  direct it and the keys, "Message the PO — @codex or @claude to direct it · #18 links a task ·
  Ctrl+Enter sends" (no `@` part in a one-agent chat); numbered asks and images have buttons.
- **With a mouse the box is one card** (layout A, #126; `@media not all and (pointer: coarse)`):
  `--surface`, a `--border-strong` edge (`--focus-ring` while you type), `--r-300`, 24px from the
  column's sides and 16px from its foot. Inside: the hint line (`#hint`, `--fg-muted`, gone when
  empty), the words with no box of their own, growing with them (`field-sizing: content`), then
  one row: **+ Add an ask** and **Attach** (Subtle; Attach opens the file picker for images, the
  same path as a paste or a drop), then **Send**, the surface's one Primary button.
- **Sent as one message the hub reads:** `## Points (N)`, the head, then one `**N.**` item per point
  with its `[image] <name>` lines inside it (an empty point is dropped; no point at all sends the
  words alone). The hub gives each item its own `P` number, chip and reminder (`points.py`), puts each
  image's stored path in its item (`message_refs.with_images`) and writes an item's `[ref …]` blocks
  inside that item. The person's balloon shows it as a numbered list, each item with its thumbnails,
  its link chips and its own point chip and control (`pointItemsHtml`); a folded row says "3 points ·
  its first words".
- **The draft** (words, points, images) is kept per room in `sessionStorage` and comes back after a
  reload; Send clears it. The editor never grows past half of the visible height (`--vv-h` on a
  phone): it scrolls within itself.
- **Phone:** every control and chip is `--touch-min`, every box `--fs-400`; the header and info bar
  step aside while any box in the editor has focus, as they did for the one box.

### Balloons

With a mouse (layout A, #126, from the mockup; the same media gate as the box's card), a chat reads
as a conversation: **your balloon is a bubble on the right** (`align-self: flex-end`, at most 85% of
the column, `--hover`, no edge), and **an agent's is plain text** on the page's ground, no box,
under its header. This reverses the old left-aligned balloons with an edge: in the 880px column of
the middle, the width they saved is not missed, and who said what reads at a glance.

- **The header** (`.from`) is `--fs-200` 400 `--fg-muted`, sentence case, the name `--fg-subtle`
  500; an agent's starts with its 20px avatar (`::before`, "C" or "X" on `--agent-*-bg/-fg`).
- The hub's rows keep their sunken rows; a send on its way keeps its dashed edge.
- **Phone:** the same, full width under the bar's two rows (#129).

### A send on its way

What the person sent that the conversation does not show yet (`sendHtml` in `session.html`; the
hub's `sends.py` keeps it). It is their balloon with a **dashed** edge and one line under it saying
where it is, in words: "Queued: it goes in once the session is up.", "Delivered, not yet read by the
agent." (with **Hide**), or "Not delivered: *reason*." with **Retry** and **Discard**.

- **Never on a timer or a row count.** It goes only when the turn that holds it is drawn (it is then
  replaced by that balloon in the same draw, never gone and back), or on Discard or Hide. Every copy
  of the chat and a reload show it, because the hub holds it.
- Queued and delivered are fine, so their line is `--fg-muted`; not delivered is wrong, so its line
  is `--c-danger-fg` with the danger edge. **No opacity:** a faded balloon took its text below 4.5:1.
- Retry · Discard · Hide are the compact buttons of `.pend-state`, `--touch-min` on a phone.

### Run chip

`● working` · `● idle` · `not started` · `not running`.

**`not running` must read as calm, never as an error** — grey text, grey dot, no red, no border, no
icon, same weight as every other card. It is the state the whole board sits in for a minute after
every hub restart. If it ever looks broken, the design has failed.

### A choice from a list, in Settings

A setting whose values the hub knows is a `<select class="pref-select">` under its `<label
class="pref-label" for>`, never free text (Settings' *Agent models*, #145, is the reference:
`renderAgentModels` in `index.html`). 32px, `--surface` on `--border-strong`, `--r-200`, the value in
`--font-mono` at `--fs-200`; `--touch-min` tall with `--fs-400` text on a phone.

- **The first option is "leave it to the other side", and says what that is now:** "Codex's own
  default (currently gpt-6-astra)". The rest are what can be chosen, fetched, never typed into the page.
- **What is in effect is also said in words under the select** (`.cfg-hint`, `role="status"`): a
  phone's select cuts a long choice short, and a consequence (the pool a model draws on) has no room
  in an option.
- **A choice is saved as it is made;** there is no Save button. The select keeps the focus and
  its place meanwhile (never disabled, never rewritten while someone is in it), so arrow keys step
  through it; choices are saved in the order they were made. A refused choice shows the hub's
  reason in the toast and the select goes back to what is saved. A value saved earlier that is no
  longer offered stays in the list, marked, until another is chosen.
- Not red, not amber: a choice is not a state.

### The task list (left)

Layout A's list (#115, built in #123 from #114's switcher): every project's tasks and POs in one
300px column (`--sw-w`) left of every desktop page, under the bar, from the top of the page to the
bottom. It is how the person gets around; the board stays the planning view. `index.html`'s
`swGroups` / `swListHtml` (the "Task switcher" block) are the reference, and `#switcher` is one
self-contained component, so a later layout can host it elsewhere.

- **A Dock panel of its own** (#137, "The list as a dock panel"): on a desktop `#switcher` is the
  `list` panel of a second dock, `#list-dock`, laid over the area under the bar (fixed, `z-index` 15,
  clicks going through it); its other panel is `#ld-page`, an empty stand-in for the page (the
  `fill`, no title bar, never moved). **Docked at the left and pinned by default**: 300px on
  `--surface-sunken` (the mockup's ground: the list is where you go, the middle what you read), the
  splitter (5px) on its right, no shadow. Its ⋯ and − are the tools' (Dock v0.5.1): View Mode
  (Dock Pinned, Dock Unpinned: a 44px left strip with its icon, sliding out beside the middle,
  Undock: over it, Float, Window), Move To (the four sides), Maximise, and − (minimise). Docked and
  slid out, ⋯ and − sit at the right end of its own head row (no tab: the filter keeps its width;
  Move To replaces the drag), so pinned it looks as before; floating, minimised or maximised it has
  Dock's full title bar. At the top or the bottom it is a 240px band, and each axis keeps its own
  size, a resized one too (Dock v0.10.0's `layout.depth`, kept in the layout: the page's
  `ldAxisSize` and its `cd-list-dock-axis` key went with #150, `ldMake` clears the old key once;
  ENSEMBLE-NEEDS item 10); slid out there it spans the width. Slid out, it
  leaves the middle 360px across or 200px down, and Dock lays out the flyout's room to the panel's
  own size (item 12; `ldInsets` only sets the page's insets now). Closing its window or the window's Hide hides it in Window mode;
  Panels reopens it even when it has no former strip button. The reload notice has its × again:
  Panels remains available for recovery. **The page
  follows the stand-in** (`ldInsets` → `--list-w`, `--list-r`, `--list-t`, `--list-b` on `body`):
  it takes the room the list leaves, unpinned, floating or in its window. Slid out, the list goes
  back on a click elsewhere, in a conversation's frame (`pdChatClicked`) or once a row is opened. In
  its own window a row still opens the conversation in the main window (the list's code stays in
  the page). The layout is kept per browser (`cd-list-dock`). Before the library loads, or where it
  cannot, `#switcher` is the fixed column (`body.sw-on` gives `main` `padding-left: var(--list-w) + 20px`).
- **On a phone it is home** (#129, `phList()`): the whole screen under a one-row bar, shown
  whenever nothing else is (no project, open task or cards page), with `main` out of the
  way (`body.ph-list`). A row opens its conversation full screen; the bar's `←` brings the list
  back where it was (its scroll and the row last opened, marked: `PH_BACK`); a task that closes by
  itself (Esc, archived, deleted: `closeTask()`) goes back there too. Every row, group head
  that folds, "Show all" and head control is `--touch-min`. Its foot is a quiet **Project cards**
  (`#sw-cards`, phone only): the project cards, with their Unassigned card, as a page of their own
  (`PH_CARDS`; also the project menu's All projects, which only a phone has), whose `←` goes back
  to the list. It stays (#135): on a phone it is the one way to a project without a PO and to
  **+ New project**, since the project menu sits in the bar's second row, hidden on the list.
- **It is the app's home and its alarm** (#135): the bell, its tray, the Needs you page, the
  desktop's home cards and the PO pill are gone. A desktop opens on the list plus the last
  conversation (`cd-last-conv`), else the first Needs you entry, else an empty middle ("Pick a task
  or a PO in the list", with **+ New project**; a search typed there shows the flat table). Every
  way that led to Needs you (the wordmark on a desktop, `#needs`, a page saved on the old Needs you
  page) lands on the list with Needs you scrolled into view and its head focused (`swShowNeeds`);
  on a phone the list comes back where it was, then scrolls there. A project chosen in the list's
  filter that would hide a Needs you entry gives way to All projects then: the bell showed every
  project's.
- **Its head** is two quiet controls, each a native `select` with its box and arrow drawn away
  (`.sw-pick`): the project filter as a chip on `--selected-bg` (`#sw-proj`: "All projects", then
  every registered project by name; it narrows what you see) and **Group: status / project** as a
  Subtle button (`#sw-by`). Both are remembered per browser (`cd-switcher-project`,
  `cd-switcher-group`), and every group follows the filter.
- **Six groups, in this order:** **Needs you** (`/api/attention`'s items less finished reports,
  oldest first: blocked or waiting POs, a PO that could not start with its message waiting, held
  PO-to-PO wakes and every task that waits on you), **Running** (live tasks not waiting for
  a check, by project and number, so a row does not jump each time its agent takes a turn),
  **Ready for your check** (In review, reported finished, or paused part way; not Done; oldest
  first), **Projects** (each project's PO, latest news first), **Unassigned** (#128: every session
  in no project, the person's own terminal sessions and tasks started without one, newest first;
  the newest 10, and the open one after them, then a quiet "Show all (N)" line, `.sw-more`; only
  sessions the page holds a row for, so every row opens; no drafts or archived ones, as in every
  group; left out while one project is chosen) and **Done today** (Done and last changed since
  midnight). Unassigned and Done today are folded in a `details`. The name is the one the home
  card, the project menu and the Move to project picker already use for the same sessions. Group
  heads are sentence case, `--fs-200` 600 in `--fg-subtle`, the count after the name in
  `--fg-muted`. **An empty group is its head alone**, quieter (500, `--fg-muted`) with its `0`: the
  list keeps its shape, so no group comes and goes under the pointer as tasks move.
- **Group: project** (`swByProject`): a head per project (its open task count), its PO first
  (once, with its lozenge if it needs you), then its tasks in the status groups' order; then
  Unassigned, which holds the tasks in no project, and Done today, both folded.
- **A row is two lines.** One: the state's 8px dot, the key (`--font-mono` `--fs-200`
  `--fg-muted`) and title (`--fs-300`, one line, ellipsis, the full text in the tooltip) and the
  age at the end (`--fs-100`, `--fg-muted`, ticks in place). The dot (`swState`) says the state in
  the colour that means it: what needs you in its lozenge's tone, working `--run-working`, news you
  have not read `--c-discovery-bold` ("new", standing in for the unread dot, and the title goes
  600), idle `--run-idle`, done `--c-success-bold`, else `--run-off`; it carries its words in
  `aria-label`. Two (`--fs-200`, `--fg-muted`, indented to the title): Needs you's lozenge, then
  one line of words, the project first: who is on a running task ("claude, codex"), or why a task
  is ready ("in review", "reported", "paused"), then `+ −`. An Unassigned row gives the folder it
  ran in, and `past session` (it opens read only) or `task, not running` when it is not running.
  A PO row is "*Project* · PO", then "N answers to check · M asks open" (or what it is doing:
  idle, working, not running). **A Needs you row** (`.sw-row.needs`) says since when it waits
  ("since 14:05", else its age), names the agent in line two, and adds a third line (`.sw-why`,
  `--fs-200` `--fg-muted`, two lines at most) with the reason; the tooltip holds the reason and a
  dead agent's last screen. A tooltip is never the only copy (§8.3), so where there is more than
  the row shows (a last screen, or a reason its two lines cut off, measured where it is drawn and
  again when the list resizes: letters, not a character count, decide) a quiet **Details** line under
  the row (`.sw-diag-btn`, a disclosure with `▸`/`▾`, `--touch-min` on a phone) opens the reason in
  full and the last screen in `--font-mono` (`.sw-diag`, on `--surface`), kept open across redraws.
  A PO's row names its agent too.
- **Selected** is `--selected-bg`: the open task, else the PO on screen (its project's page, or
  its drawer). Its muted words step up to `--fg-subtle` there and under the pointer (`--fg-muted`
  and `.tno` on `--selected-bg` or `--hover` are under 4.5:1 in Light).
- **A row opens what it names where it opens today:** a task in its panel over the page you are
  on; a PO on its project's screen (the PO chat revealed).
- **It never moves under the pointer:** while hovered, rows keep their group and place
  (`swFreeze`); what changed lands when the pointer leaves. It redraws with the board's refresh and
  `/api/attention`'s, no poller of its own.

---

## 5. Layout and navigation rules

**Three fixed zones and one overlay.** Top bar (global, thin) · the task list on the left (*where
you can go*, §4) · content (*what you are looking at*) · the issue view as an overlay. Nothing else moves.

1. **Nothing about the current view goes in the top bar.** The bar carries identity, where-am-I,
   search, what-needs-me, me, create. Filters, counts, grouping and sort belong to the view that owns
   them. This rule is what keeps the bar thin after the next five features — it is the reason the bar
   got heavy the first time.

   **One bar, one order, at every width** (#107). Left to right: **where you are** (the
   wordmark, which is the way home, `/`, then the project's name as a Subtle button with a caret:
   its menu switches project and holds the project's settings, its kind and key and, for a
   documents project without one, setting up its PO; on a desktop this is the breadcrumb, see
   *The bar's breadcrumb* below), then **what you can do here** (`#bar-here`:
   a phone's PO screen's **Panels ▾**, `pdCtlHtml`; empty elsewhere), then the global group: search,
   the plan chip, the avatar and **Create** (in a project, Create opens the new
   task dialog with that project chosen). Panels is the one control about the page below that the
   bar carries: a PO screen has no other row to hold it, and the bar is where the page's own
   controls start. Nothing else joins `#bar-here` without replacing something.

   **The wordmark** (#108; `#bar-home`, drawn by `tools/make_wordmark.py`, which writes the SVG
   between its markers in `index.html`) is ENSEMBLE in the icon's bars: the icon is its E, the
   other letters are bars and stems on the icon's grid in `--fg`. It is the link home (on a desktop,
   the list's Needs you; on a phone, the list), named
   "Ensemble, all projects" (`aria-label`; the SVG is `aria-hidden`, the icon's `alt` empty).
   - **Sizes:** the icon 22px and the letters 15.84px (the E inside the icon), 125 × 22px in all (124.5 in the SVG),
     in a 32px link (44px on a phone). Never scaled to fit: where the word does not fit, the icon
     alone (`img.logo`, 22px) takes its place. The word shows above 900px and on a phone from
     410px (row one, on home and in a project, with the back arrow and the name on row two); the
     icon alone from 641px to 900px (a long project name and the plan chip leave the word no room
     at 768) and on a phone under 410px (row one's controls take 262px).
   - **Colour:** tokens only, so it reads in every theme: `--fg` for the letters, and the icon's
     own colours inside its tile. The other two variants (`lanes`: each E the icon's coral, mint
     and amber bars; `gradient`: the word in the icon's blue to violet) read `--wm-lane-1…3`
     and `--wm-from`/`--wm-to`, set in each theme block and measured at 4.88:1 or better on its
     `--surface`. Switching is `py tools/make_wordmark.py lanes`; `--preview DIR` draws all three in
     every theme with the contrast table.
   - Nothing else joins it: no product name in text, no badge but a non-default instance's.

   **The bar's breadcrumb** (layout A, #124; a desktop, `body.mid`; `crumbsOf`, `barCrumbs` and
   `crumbGo` in `index.html`, tested in `tests/test_middle.py`): one line after the wordmark,
   `ENSEMBLE / project ▾ › #18 the task (or PO) › its tool › the file`, four levels at most.
   - **Every part is at the body size** (`--fs-300`, #126, as the mockup): weight says where you
     are, not size.
   - **The project** is `#proj-go`, `--fg` at `--fs-300` 400, a Subtle button that goes up to its
     PO's conversation (closing an open task), or to its board when it has no PO. The caret beside
     it (`#proj-switch`, its name hidden) keeps the project menu. With no project (home, or a task
     in none) `#proj-switch` shows its name as before.
   - **Then** the conversation: `PO` on a PO screen, or the open task's number and title; the
     tool (a task's tab other than Activity, or a PO screen's tab other than PO chat, or a project
     without a PO's Workspace or Changes tab); the file open in that Workspace or Changes, by its
     name, the whole path in its tooltip.
   - **Every part but the last is a Subtle button that goes up one level**, in `--fg-subtle`:
     the conversation closes the tool and keeps the conversation (a task's Activity; a PO screen's
     PO chat); the tool goes back to its list (Changes: the diff closes; Workspace: the tree, the
     file shown in it). The last part is where you are: plain `--fg`, 500, `aria-current`.
   - **A file that is a panel of the dock** (#150, `crumbWsPanel`): while its panel is on screen
     (in front of its stack, or in a window of its own) it is where you are, `› Files › README.md`,
     whether or not the tree is out; its Files part brings the tree (the flyout, a phone's Files
     tab) with the file shown in it, and the file part stays, since the panel is still there.
   - Separators are `›` in `--fg-muted`, not read aloud. Each part ellipsises, the task's title
     first (`flex-shrink: 4`), so the bar keeps one row; search gives up width before it does.
   - Written only when it changes (`writeSlot`). **A phone** shows it only while a task is open,
     in the bar's second row (#129): `← project ▾ › #18 title › tab › file`, every part
     `--touch-min` tall; the tab is the dock's tab in front of the Chat (`crumbState`), and the
     task's part brings the Chat back. The project's part goes to its screen; `←` goes to the list.

   **The global group on a desktop** (layout A, #126): where you are fills the left, so only the
   gap before search grows. **Search** is a quiet box of about 300px on `--surface-sunken`
   (`--surface` while you type), "Search or jump to…", with its key as a `kbd` (`/`, the page's
   shortcut; the mockup's ⌘K does not exist) that goes while you type. It is never focused on load:
   that made `/` useless. The bar has no PO pill and no bell (#135): each PO is a row of the list,
   and what needs you is the list's Needs you.

   **A project page has no rows of its own above its content.** No status row: what needs you is the
   list's Needs you; what changed is on the Changes panel tab (a badge) or the Changes tab (a
   `.ptab-n` count), and on a phone's cards page in its top row. No crumbs
   row: the project's name is in the bar. A project without a PO keeps one row, its tabs
   (`.ptabs`) with the board/list switch at their end. `--chrome-h` is the bar plus that row;
   re-measure it if a row is ever added (a Workspace or Changes tab must fit the screen, no page
   scroll).

   **A PO is a row of the list, not a control in the bar** (#135: the pill is gone). Its row in
   Projects names it and carries the person's own points with it ("N answers to check · M asks
   open", see *Points* in §4) and its lozenge only when it needs you. It opens on its project's
   screen, the PO chat revealed; on a desktop an open task in the middle gives way to it (the
   middle holds one conversation), and a phone does the same (#129, `poMidGo()`). The drawer
   (`openPoOf`, `PO_PEEK`) is left only for a page whose dock failed to load.

   **A live session is built once and never reloaded by navigation.** The PO's conversation is one
   iframe in `#po-panel`. On the PO screen it sits inside the PO chat panel; everywhere else it is a
   fixed drawer. It moves between the two only through `pdPlaceChat`/`pdMove`, which use
   `moveBefore` so the iframe keeps its page (a plain `appendChild` would reload it; browsers
   without `moveBefore` do reload it then). Never rebuild it, and never move it any other way.
   **The middle** (layout A, #124; a desktop, `body.mid`, set by `midSync` from `MOBILE_MQ`).
   The screen is list (left, the task switcher, `--list-w`) | middle | the tool strip (#125, see
   *Panels* in §4). The middle holds **one conversation**, full height under the bar: a project's PO
   or a task's. Both are the dock's middle: an open task's `#detail-panel` moves into the
   conversation's place (`pdPlaceTask`, with `moveBefore`, so its live chat keeps its page;
   `body.dp-docked`), its tabs go, and its Changes, Workspace, Spec and Details panes move into the
   tools (`dpPlacePanes`; a tool holding a task's pane shows it instead of the PO's, `.pd-for-task`).
   Opening a task on a desktop makes its project the page's (so Your asks and the Board are its
   project's; closing it lands there), also for a project without a PO, whose open task gets the
   strip too. Its text keeps to a centred column of `--conv-w` (880px): the chat's iframe, the
   task's header. Opening a task replaces what the middle showed; closing it (×, Esc, the project's
   crumb, the PO's row) gives it back. The page does not scroll; each pane scrolls inside itself.
   Where the dock cannot load, the task panel has its tabs as in step 2 (`Activity · Changes ·
   Workspace · Spec · Details`, see *Tabs*).
   **A phone** (#129) has no list beside it: one screen at a time. The list is home (§4 *The task
   list*); a row opens the conversation full screen, in the same dock (narrow: its tools are tabs,
   see *Panels*), and the project becomes the page's there too. `←` in the bar closes it and brings
   the list back where it was.
2. **The board groups; it never sorts.** Priority first, then most recently updated, in *both* views.
   The hover-freeze works by being the **only** place ordering happens — any second sort defeats it and
   cards move under the cursor. Two tasks must never swap places because someone flipped the view
   switch.
3. **Nothing moves under the cursor.** Order *and* grouping are frozen while the mouse is over the
   view. Applies to agent-driven column changes too.

   **The freeze holds placement, not content.** A card's column and its position in that column are
   frozen; its run dot, attention lozenge and elapsed time keep updating. The cheapest way to stop
   things moving is to stop re-rendering, and that is wrong — *what is running now* is one of the four
   questions this design answers, so a board that goes stale under the cursor fails as badly as one
   whose cards move under it. A dot changing colour in place moves nothing.

   **The same holds inside a panel built once.** The task panel is a shell (a header slot and panes)
   built once per task, so its live session, its file viewer and an unsent comment survive a refresh.
   Give each slot its **own** signature: the header is rewritten whenever status, attention, workflow,
   priority or the summary change; a pane is rebuilt only when what it shows changes. One signature
   for both either freezes the header on a stale "● working" or rebuilds the panes and loses their
   state. Key the shell on the task alone, and never rewrite a slot while a menu inside it is open.

   **Nothing a person can click is replaced on a timer.** Text that changes on its own, such as an
   elapsed time like "updated 11s ago", is kept out of every render signature and patched in place;
   a view is rebuilt only when what it shows changes. A click whose press and release straddle a
   replacement lands on two different elements and the browser never fires it, so a rebuild on every
   refresh makes controls silently dead on exactly the busy tasks people watch. It also drops keyboard
   focus to `<body>`. When a genuine change does rebuild a header, put focus back on the equivalent
   control. The board's cards are the reference: `.cwhen` updates in place and the card stays the
   same element.

   **Strip the text, keep the timestamp.** The signature drops only the ticking *text* and keeps the
   timestamp it is computed from: `<span data-ago="1788975798">` keeps its attribute. Then the clock
   passing changes nothing, while a real new timestamp, such as an agent posting, still counts as a
   change and resets the age. Stripping the whole span would freeze every age at its first value,
   trading dead clicks for stale content. `withoutAgo()` in `index.html` is the reference.

4. **A filter may persist only while its state is visible in the filter row.** Filters survive a
   reload, which is safe *because* the chips show what is active — the menu hides the controls, never
   the state. If the chips are ever removed, persistence must go with them, or the user returns to a
   list silently hiding rows with nothing on screen saying why.
5. **Never show an absence without its reason.** Whenever the UI shows less than everything, the cause
   is on screen next to the gap — a dismissable chip for each active filter, `+ N older` under an aged
   Done column, and an empty tab that says what would fill it (*"No branch yet — nothing has run."*).
   A gap with no visible cause is read as a bug, and it is the single most common navigation failure:
   a task looks like it vanished when in fact something is hiding it. This rule generalises three
   separate cases; treat any new one the same way.

6. **One badge per question.** `Needs you` has a count, `Active` has a count, a project has a count. No
   other number in the chrome.
7. **Colour is never the only signal.** Every lozenge is a word; every dot has a chip beside it. The
   design must survive being printed in grey.
8. **A card never drops a signal to fit.** Board columns never wrap and never shrink below 248px;
   horizontal scroll is the escape valve. Page gutter 24px, 16px below 900px.

   On a project's PO screen the board is the Board panel (see *Panels* in §4), however wide the
   person makes it; its pane scrolls both ways. On a desktop, whenever columns lie past the edge of
   the pane, it shows a fade and a `›` naming them. A phone swipes its board and gets neither. (The
   *Board wide* and *Board beside the PO* layouts of 2026-09-11 gave way to the panels.)
9. **No destination pages: the list is where you go** (#135). `Needs you` and `Active` were once
   pages that had to replace whatever view was showing, from every starting view; a control that
   looked as if it responded and did nothing was the bug they kept having. Needs you is now the
   list's first group, always on screen on a desktop, and every way that led to it lands there
   (`swShowNeeds`). A new place to go is a group or a row of the list, not a page with its own
   route; if an old address has to keep working, send it to the list.

### Attention vs. columns — a boundary that will decay if you let it

`Needs you` means **something is stuck or waiting on a reply right now** — it clears itself when the
condition ends. It is not a to-do list. A project's PO is no task, and appears there (named `PO`,
opening its conversation) only when it is `blocked`, `waiting_for_you` or `agent_gone`, never idle
or `stalled`.

*In review* is a **column**, not an attention state: it is queued work for the owner, which is what a
column is for. Do not feed columns into the notification count, or the count stops meaning "unblock
something" and starts meaning "everything outstanding", at which point nobody reads it.

Notifications have **no read/unread state**. An entry exists if and only if the condition holds. The
only exception is `agent_gone`, which nothing else can clear, so it gets an explicit Dismiss.

---

## 6. Board and workflow

Columns are **Backlog · To do · In progress · In review · Done**, from the persisted `workflow` field.

Run state is shown on the card and is **never** a column. This is not a style preference: run state is
computed from an in-memory PTY registry, so columns built on it empty into the last column on every hub
restart, and cards move between columns as agents finish asynchronously.

Transitions are a mix of automatic and human — see `design.md` §3.2 for the current matrix. Two rules
regardless of who moves a card:

- **An agent may declare its own transition** (it knows its own intent) but **must never infer one from
  silence.** "Finished quietly" and "stuck" are indistinguishable from outside a live PTY; the codebase
  already documents this surrender in `attention.py`.
- **The owner's drag always wins**, and is always available for every column.

The hub moves a card by itself in one case: **merged work goes to Done.** When a task's branch has
commits of its own and all of them are on `main`, the PO's progress check (`digest.py`) moves the card
to Done. This is the PO's merge acting — a fact in git — not an inference from silence. It moves a card
once per merge, and not at all if the card was moved after the merge (`workflowAt` against the time
`main` first held the work), so a card the owner drags back out of Done stays out. A branch with no
commits yet is never read as merged, although it too has nothing ahead of `main`.

Agent-driven transitions are also described in the **`ensemble` skill**, which every agent reads. If
you change transition behaviour, change both, or they drift.

---

## 7. Before you commit

- [ ] No raw hex in any rule — tokens only
- [ ] `--accent` used only where §1 allows
- [ ] Nothing new is red unless it is wrong or destructive
- [ ] Any new colour pair measured, ≥ 4.5:1 in **every** theme
- [ ] No new `box-shadow`, radius, font weight or spacing value outside the scale
- [ ] No `opacity` on text, and nothing below `--fs-100`
- [ ] A state that is fine carries no colour; an uncertain one is amber and marked by a glyph
- [ ] A panel's header updates on a status change without rebuilding its panes
- [ ] Nothing clickable is replaced on a timer: ticking text is patched in place, and a refresh
      keeps the same elements
- [ ] Every way to Needs you (the wordmark on a desktop, `#needs`) lands on the list's Needs you
- [ ] No hard-coded header/chrome offset
- [ ] At most two lozenges on a card or row
- [ ] Nothing added to the top bar that belongs to a view
- [ ] Checked at a narrow laptop width **and** wide, in Light first and then every other theme
- [ ] The list still does not reorder while the mouse is over it
- [ ] Checked at 360 and 430px in an iframe, and at a landscape phone size: §8 holds, and 1280 and
      1440 measure the same as before the change

---

## 8. Phone

The dashboard, the chat and the file view are used from a phone. A phone is a touch screen of 360–430px
in portrait, about 390px tall in landscape, with an on-screen keyboard that takes half the height. The
audit that wrote this section is `phone-audit.md` in the task folder for *Make Ensemble work properly on
a phone*.

**Which query.** `index.html` switches to its phone layout at
`(max-width: 640px), (max-width: 1024px) and (pointer: coarse)` — `MOBILE_MQ` in the script, the same
text in the stylesheet. `session.html` and `fileview.html` use **`(pointer: coarse)` alone**: on a
desktop they sit in the task panel, the PO's drawer and the Workspace pane, all narrower than 640px, and
a width query would restyle them there. Phone rules go inside those blocks and nowhere else, so the
desktop is untouched by construction.

1. **Every tap target is at least `--touch-min` (44px)** in both directions. When the look must stay
   small (a 16px lozenge that is also a picker), give it an invisible `::after` margin instead of a
   bigger box — and remember a sideways-scrolling row clips that margin, so the row carries it as
   padding.
2. **Every field is `--fs-400` (16px) on a phone.** A phone zooms the page into any focused field
   smaller than that and leaves it zoomed.
3. **Nothing needs hover.** Whatever a pointer reveals on hover is simply shown. A `title` tooltip may
   add detail, never carry the only copy of something.
4. **The bar fits 360px: six 44px targets and Create.** Identity shrinks to the icon under 410px
   (the wordmark from 410px, §5.1); search folds into a button and
   opens over the bar, and stays open while it holds a query (§5.5: a filtered list shows why); the
   plan chip moves into the avatar menu, and its warnings still reach the banner. Anything new for the
   bar on a phone must replace something, not squeeze it.

   **Home is one row; everywhere else is two** (#129). On home, the list, the bar is one row.
   Anywhere else (a project, an open task, the cards page) the bar wraps (`body.row2`):
   row one is the wordmark, then search, the avatar and Create; row two is the
   back arrow `←` (`#bar-back`, to the list; phone only, the wordmark is the way back elsewhere),
   the project's name with its menu, truncated, where you are in it (an open task's breadcrumb) and
   Panels at the end. `--header-h` is redeclared on `body.row2` so everything hanging off the bar
   follows. At 430×932 the PO screen's panel tabs start at 110px.
5. **An open task never covers the bar.** The bar is the way to the PO and to the project. On a phone
   the task takes the whole width *under* the bar's two rows and the dock's tabs, and the PO's row
   in the list goes to the PO's screen (the task closes, as on a desktop).
6. **The keyboard never covers a composer.** The viewport tag carries
   `interactive-widget=resizes-content` (Android then shrinks the page); for iOS, which shrinks only
   the visual viewport, the pages set `--vv-h`/`--vv-top` from `visualViewport` and size anything
   pinned to the bottom from them. Never pin a composer with `100vh`.
7. **A popover or comment box opens at the top, not the middle.** Centred, it sits under the keyboard
   it opens.
8. **Touch selection is followed through `selectionchange`**, not `touchend`: the handles adjust the
   selection after the finger lifts.
9. **Two panes side by side become one above the other** below the phone breakpoint, or one at a
   time (Changes: the files, then the diff with **‹ Files** back, §4 *Changes*). Beside each other on
   390px, the file got 79px.

   **The Workspace shows one pane at a time** (`wsPaintPane`, class `ws-tree` on `.wsp`): the file,
   whole height, when a tab is shown; the tree and Find when there is no file, or when asked for
   (**Files**, first in the path bar; Show in the tree; a folder in the path). From the tree, the
   file's name with `›` (at the end of the Find row) goes back to it. Both panes share one grid
   cell; the one not showing is `visibility: hidden`, never `display: none`, so the file's viewer
   keeps its page and size. Neither switch shows off a phone.
10. **Measure it.** In this environment `(pointer: coarse)` never matches, so to measure a phone's
    landscape layout rewrite the query in a test copy of the page (the audit's proxy does it for
    `?_coarse=1`), and give the frame's scrollbars zero width: a desktop frame's 9px scrollbar makes
    every layout 9px narrower than on the phone it stands for.
