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
  pair in §3 on it. The ground picks the code-highlight stylesheet and which way the accent's hover
  mixes.
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
because it is the reason you would look at the card) → title → priority arrow + run chip + project →
agents with models + elapsed.

Four signals must be legible **without hovering**: status, priority, assignees with their models, and
any attention state. Models are shortened on a card (`gpt-5.6-luna` → `luna`); the full string lives in
the issue view. Three or more agents show two, then `+1`.

### Avatar

20px, `--r-full`, **solid** fill from the agent tokens with light text. Solid fill is load-bearing, not
styling: it is what stops an avatar reading as a lozenge. Overlap −6px when stacked with a
`2px solid var(--surface)` ring.

### Tabs

A tab set is **fixed**: the same tabs, in the same order, with the same names, whatever state the
thing is in. The task panel is always `Activity · Changes · Workspace · Spec`. What may change is the
pane behind a tab (a live session or a transcript under Activity; files, or "this task has no folder
yet", under Workspace) and which tab is selected by default. Never the set, the order or a label. A
tab that renames itself when a task starts is a quieter version of tabs that reorder.

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
9. **A destination replaces whatever view is showing.** `Needs you` and `Active` must open from
   anywhere: the home page, and a project's Tasks, Changes or Workspace tab. So their branch runs
   first when the view is drawn, and anything that navigates into a project clears them. They once
   worked only from inside a project's Tasks tab, which is where they had been tested: from the page
   the owner opens on, the sidebar item highlighted and nothing else changed. A control that looks as
   if it responded and does nothing is worse than one that is visibly off. Test a destination from
   every starting view, not just one.

### Attention vs. columns — a boundary that will decay if you let it

`Needs you` means **something is stuck or waiting on a reply right now** — it clears itself when the
condition ends. It is not a to-do list.

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
