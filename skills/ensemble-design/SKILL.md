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
  to the avatar menu and to the hub's allowed `theme` values in `dashboard.py`, then measure every
  pair in §3 on it, including its `--code-*` set in `fileview.html` (see *Code* below). The ground
  picks which way the accent's hover mixes.
- **The choice follows the person, not the browser.** It is stored on the hub (`settings.theme`) so
  every device agrees. `localStorage['cd-theme']` is only the first-paint cache: the head script
  applies it before first paint, then fetches the hub's value and adopts it if it differs. If the
  hub has none yet, the page hands the hub its own, so a choice made before is kept.
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

`--header-h` (49px: 48px bar + 1px border) · `--chrome-h` · `--sidebar-w` · `--detail-w`.

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

**File tabs are not panel tabs.** Inside the Workspace pane, each open file is a tab above the viewer,
as in an editor; these come and go as files are opened and closed, so the fixed-set rule does not
apply to them. Their rules instead:

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
- **Phone:** the box and a few rows sit above the file (45% of the height, 60% with a query); the field
  is `--fs-400` and `--touch-min` tall, every toggle and result row `--touch-min`, and a matching line
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
the reference. It is information for the person, not an alarm: **no colour of its own, never the
bell's count.**

- **The line** (`#points-line`) sits under the ask line, in its look (`--surface`, `--border`,
  `--r-200`, `--fs-200`, `--fg-muted`): "Your points: 2 waiting for an answer · 3 answered, not yet
  acknowledged". Hidden when both are 0. It is a disclosure button (`aria-expanded`, a `▸`/`▾`
  glyph) that opens a compact list under it, at most 40% of the height, scrolling within itself.
- **A list row:** the id in `--font-mono`, its first words (`--fg`, one line, cut with `…`, the
  whole text its tooltip), its age ("12m", patched in place, never a redraw), its state in words,
  "your message ↑" and "answer ↓" as links (`--link`), and its one click: **👍 Ack** for an answer,
  **Drop** for one still waiting. Answers to acknowledge first, then the waiting ones, oldest first.
  An answer given by doing shows its summary under the row.
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
  whatever would fold it (age, its task's row, Just us), and the catch-up line names "N answers to
  your points to acknowledge" first.
- **Outside the chat:** the task card (`.cpts`, beside the cost) and the PO's header and pill say
  "2 to acknowledge · 1 open" in `--fg-muted` words, the tooltip in full. No lozenge: a card keeps
  its two.
- **Phone:** the summary, every control and every link are `--touch-min`.

### Run chip

`● working` · `● idle` · `not started` · `not running`.

**`not running` must read as calm, never as an error** — grey text, grey dot, no red, no border, no
icon, same weight as every other card. It is the state the whole board sits in for a minute after
every hub restart. If it ever looks broken, the design has failed.

---

## 5. Layout and navigation rules

**Three fixed zones and one overlay.** Top bar (global, thin) · left sidebar (*where you are*) ·
content (*what you are looking at*) · the issue view as an overlay. Nothing else moves.

1. **Nothing about the current view goes in the top bar.** The bar carries identity, where-am-I,
   search, what-needs-me, me, create. Filters, counts, grouping and sort belong to the view that owns
   them. This rule is what keeps the bar thin after the next five features — it is the reason the bar
   got heavy the first time.

   **The PO pill is identity, not view state.** It names who you talk to about the current project,
   the way the avatar names you: it reads the same on every page, and nothing about the view below
   (filters, tab, board or list) changes it. It opens the PO's conversation as a drawer over the page
   you are on, so reading a task never costs you the PO. It shows no status: anything that
   needs you already reaches the bell. The one number it carries is the person's own points with
   the PO (see *Points* in §4), as quiet `--fg-muted` words after the name ("2 to acknowledge · 1
   open"), never a badge, and gone with the name when the bar runs short. On the project's Overview tab, where the PO already leads the
   page, the pill focuses its composer instead of opening a second copy. (In the *Board wide*
   layout of rule 8, the PO does not lead the Overview's Board view, so there the pill opens the
   drawer.)

   **A live session is built once and never re-parented.** The PO's conversation is one iframe in
   `#po-panel`, a sibling of `#view`. It is a grid cell on the Overview tab and a fixed drawer
   everywhere else, and only its classes change between the two. Moving an iframe in the DOM reloads
   it, so navigating must restyle it, never move it.
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
8. **A card never drops a signal to fit.** Board columns never wrap and never shrink below 248px,
   except in *Board beside the PO* below; horizontal scroll is the escape valve. Page gutter 24px,
   16px below 900px.

   A project's Overview offers two board layouts, both kept (the CEO's choice, 2026-09-11).
   **Board wide** (the default) gives the board the whole Overview in Board view and keeps 248px
   columns; the PO becomes the pill's drawer, restyled and never moved. **Board beside the PO**
   keeps the split and narrows the columns to 208px, and a card wraps its rows rather than dropping
   anything. On a desktop, whenever columns lie past the edge of whatever scrolls the board (the
   pane, or the board itself when stacked under the PO below 900px), both layouts show a fade and a
   `›` naming them. A phone swipes its board and gets neither.
9. **A destination replaces whatever view is showing.** `Needs you` and `Active` must open from
   anywhere: the home page, and a project's Overview, Changes, Workspace or Roadmap tab. So their branch runs
   first when the view is drawn, and anything that navigates into a project clears them. They once
   worked only from inside a project's Tasks tab, which is where they had been tested: from the page
   the owner opens on, the sidebar item highlighted and nothing else changed. A control that looks as
   if it responded and does nothing is worse than one that is visibly off. Test a destination from
   every starting view, not just one.

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
- [ ] Needs you and Active open from the home page and from every project tab
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
4. **The bar fits 360px: six 44px targets and Create.** Identity goes; search folds into a button and
   opens over the bar, and stays open while it holds a query (§5.5: a filtered list shows why); the
   plan chip moves into the avatar menu, and its warnings still reach the banner. Anything new for the
   bar on a phone must replace something, not squeeze it.
5. **An open task never covers the bar.** The bar is the way to the PO and to the project. On a phone
   the task takes the whole width *under* the bar, and the PO's pill opens the PO over it.
6. **The keyboard never covers a composer.** The viewport tag carries
   `interactive-widget=resizes-content` (Android then shrinks the page); for iOS, which shrinks only
   the visual viewport, the pages set `--vv-h`/`--vv-top` from `visualViewport` and size anything
   pinned to the bottom from them. Never pin a composer with `100vh`.
7. **A popover or comment box opens at the top, not the middle.** Centred, it sits under the keyboard
   it opens.
8. **Touch selection is followed through `selectionchange`**, not `touchend`: the handles adjust the
   selection after the finger lifts.
9. **Two panes side by side become one above the other** below the phone breakpoint (Workspace,
   Changes). Beside each other on 390px, the file got 79px.
10. **Measure it.** In this environment `(pointer: coarse)` never matches, so to measure a phone's
    landscape layout rewrite the query in a test copy of the page (the audit's proxy does it for
    `?_coarse=1`), and give the frame's scrollbars zero width: a desktop frame's 9px scrollbar makes
    every layout 9px narrower than on the phone it stands for.
