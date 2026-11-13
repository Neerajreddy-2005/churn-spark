---
name: Churn-Spark
description: A warm retention workbench; an apricot frame holds the tools, a near-white sheet holds the work, cobalt marks data and the one action, red only ever means customer risk.
colors:
  frame: "#ffd49a"
  frame-ink: "#2c1f0b"
  sheet: "#fffdf9"
  raised: "#faf6ef"
  ink: "#1e1810"
  muted: "#645b4f"
  line: "#ece5da"
  rule: "#f3eee6"
  control: "#cdc5b8"
  cobalt: "#2a54c4"
  cobalt-hover: "#2349ad"
  cobalt-tint: "#e9eefa"
  on-cobalt: "#ffffff"
  risk: "#b42318"
  risk-tint: "#fdecea"
  risk-ink: "#8a1a12"
  ok: "#247b50"
  ok-tint: "#e1f5e9"
  ok-ink: "#145034"
  warn-tint: "#fff4dc"
  warn-ink: "#8a4a0b"
  neutral-tint: "#f1ece4"
  selection: "#c9d6f6"
  cobalt-dark: "#7d9cf2"
  frame-dark: "#33281a"
  sheet-dark: "#1a1714"
typography:
  title:
    fontFamily: "Bricolage Grotesque Variable, Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  section:
    fontFamily: "Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.35
  kpi:
    fontFamily: "Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Manrope Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  control: "8px"
  panel: "12px"
  sheet: "16px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "20px"
  lg: "28px"
  topbar: "56px"
  sidebar: "216px"
  sheet-inset: "8px"
components:
  button-primary:
    backgroundColor: "{colors.cobalt}"
    textColor: "{colors.on-cobalt}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.cobalt-hover}"
  button-secondary:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "36px"
  button-secondary-hover:
    backgroundColor: "{colors.raised}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "36px"
  input:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 11px"
    height: "36px"
  panel:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.panel}"
    padding: "18px 20px"
  sheet:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.sheet}"
    padding: "28px 32px 40px"
  pill-risk:
    backgroundColor: "{colors.risk-tint}"
    textColor: "{colors.risk-ink}"
    rounded: "{rounded.pill}"
    height: "22px"
  pill-ok:
    backgroundColor: "{colors.ok-tint}"
    textColor: "{colors.ok-ink}"
    rounded: "{rounded.pill}"
    height: "22px"
  nav-item:
    textColor: "{colors.frame-ink}"
    rounded: "{rounded.control}"
    height: "36px"
    padding: "0 10px"
---

# Design System: Churn-Spark

## Overview

**Creative North Star: "The Warm Workroom"**

An apricot frame holds the tools; a near-white sheet holds the work. The frame is the only place warmth is loud. Inside the sheet the palette goes quiet: warm-neutral ink and hairlines, cobalt for data and the single primary action, red reserved for a customer at risk. The product is used all day by a retention team, so density is calm, tables are plain, and every number can explain itself on hover.

The system is "framed sheet": an L-shaped frame (sticky 56px top bar, sticky 216px sidebar) around a sheet inset 8px, with 16px corners and a 1px edge. Nothing has a resting shadow. Depth appears only on things that float. Motion is exponential and purposeful (arrivals ease out, exits ease in, shared-element moves ease in-out), and everything collapses under `prefers-reduced-motion`.

The palette "Mango" was chosen by the user from 10 live previews. Light and dark both ship; dark follows the system with a manual toggle (`[data-theme]`).

**Key Characteristics:**
- Apricot L-frame + near-white sheet; no resting shadows.
- Cobalt = data and the one primary action. Red = customer risk only.
- Bricolage Grotesque for page titles, wordmark and sign-in headline only; Manrope with tabular figures for everything else.
- Money has no currency symbol (the dataset has none); percentages show 2 decimals.
- Motion has a fixed vocabulary of three easings and is fully reducible.

## Colors

A warm apricot frame and cream-neutral sheet carrying one cobalt voice and a strictly scoped red. Values below are the light theme with dark counterparts in parentheses. Tokens live as CSS custom properties on `:root` in `frontend/src/index.css`; dark values sit under `prefers-color-scheme: dark` (unless `data-theme='light'`) and `[data-theme='dark']`.

### Primary
- **Cobalt** (#2a54c4; dark #7d9cf2): the primary button, links, focus ring, chart bars, selected rows, toast timer. Hover #2349ad (dark #97b0f5). Tint #e9eefa (dark #232b40) for bar tracks, hovered/active rows and selected menu items.

### Secondary
- **Apricot Frame** (#ffd49a; dark #33281a): the top bar, sidebar and the page background behind the sheet. Ink on frame #2c1f0b (dark #f3e9d9). Hover/active overlays are 7% and 12% of the frame ink.

### Tertiary
- **Risk Red** (#b42318; dark #f2675a): the risk meter fill. Pills and text use Risk Ink #8a1a12 (dark #ff9a8f) on Risk Tint #fdecea (dark #3a1d1a).

### Neutral
- **Sheet** (#fffdf9; dark #1a1714): the work surface and panel background.
- **Raised** (#faf6ef; dark #221e1a): hover fills, code chips, skeletons.
- **Ink** (#1e1810; dark #f3efe9): text. **Muted** (#645b4f; dark #b8b1a7): secondary text and labels.
- **Line** (#ece5da; dark #34302a): panel borders, table row rules. **Rule** (#f3eee6; dark #2a2621): quieter divider.
- **Control** (#cdc5b8; dark #4d473f): input and secondary-button borders.
- **Status**: OK #247b50 (dark #4cc985) with ok-tint #e1f5e9 / ok-ink #145034; warn-tint #fff4dc / warn-ink #8a4a0b (also the medium-risk meter); neutral-tint #f1ece4.
- **Overlay** rgba(30,24,16,.32) (dark rgba(0,0,0,.5)); **Selection** #c9d6f6 (dark #34467a).

### Named Rules
**The Red-Means-Risk Rule.** Red appears only for a customer at risk (high-risk pills, the risk meter, risk timeline nodes, failed/rejected data states). Destructive admin actions are neutral outlines on purpose, protected by explicit steps rather than by colour.

**The One Voice Rule.** Cobalt marks data and the single primary action. The frame and chrome never take the accent colour.

**The Not-By-Colour-Alone Rule.** Every status pill carries a text label (and a dot); colour never carries status alone.

## Typography

**Display Font:** Bricolage Grotesque Variable (fallback Manrope Variable, Segoe UI, system-ui, sans-serif)
**Body Font:** Manrope Variable (fallback Segoe UI, system-ui, sans-serif), with `font-variant-numeric: tabular-nums` on body
**Mono:** ui-monospace, Cascadia Mono, Consolas (inline `code` only)

**Character:** Bricolage brings a friendly, slightly quirky voice to the few places a person reads a title; Manrope keeps everything else clean and numeric. Both are self-hosted via `@fontsource-variable`.

### Hierarchy
- **Title** (650, 28px / 24px under 900px, 1.15, -0.02em, Bricolage): page h1 and the customer ID. Also the wordmark (700, 17px) and the sign-in headline (700, clamp(30px, 4.3vw, 68px), -0.035em).
- **Headline** (700, 18px, 1.3, -0.01em): h2. The welcome heading is Bricolage 650 at 24px.
- **Section** (700, 16px, 1.35): h3, panel titles.
- **KPI** (600, 26px / 22px small, -0.02em): headline figures. The risk meter figure is 34px / 600.
- **Body** (400, 14px, 1.5): default; table cells 13.5px.
- **Label** (500-600, 12-12.5px, no tracking, sentence case): table headers (500, muted), field labels, notes.

### Named Rules
**The Two-Face Rule.** Bricolage is for page titles, the wordmark and the sign-in headline. Never for body, tables, buttons, labels or KPI figures.

**The Tabular Rule.** All figures use tabular numerals; numbers in tables are right-aligned.

## Layout

A CSS grid shell: `216px | 1fr` by `56px | 1fr`, with the top bar spanning both columns. The sheet's inner content is capped at 1180px and centred. Page sections stack with 24px gaps; panels sit in grids with 20px gaps (`grid-2` 5fr/7fr, `grid-even`, `grid-3`). Sheet padding is 28px 32px 40px (20px 16px 32px on phones). Panel padding is 18px 20px.

At 900px and below the sidebar becomes a horizontally scrolling row under the top bar, the search trigger collapses to a 36px icon, grids go single-column, the KPI strip becomes 2x2, and the sheet inset shrinks to 6px. `grid-3` collapses at 1100px. Sign-in is a two-pane layout (canvas scene + 380-520px sheet) that stacks at 900px.

## Elevation & Depth

Flat by default. Depth is tonal: frame, sheet, panel edge (1px line), raised fill. The sheet edge is a 1px border of ink at 9%. Shadows exist only for floating things.

### Shadow Vocabulary
- **Float** (`0 12px 32px rgba(44,31,11,.16), 0 2px 6px rgba(44,31,11,.08)`; dark `0 12px 32px rgba(0,0,0,.5), 0 2px 6px rgba(0,0,0,.3)`): account menu, explain popover, toasts, command palette, select menu.
- **Focus glow** (`0 0 0 3px` cobalt at 22%): focused or open inputs and select triggers.

### Named Rules
**The Float-Only Rule.** No resting shadow on sheet, panels, KPI strip, buttons or table rows. Only elements that detach from the page (menus, popovers, toasts, palette) take Float.

## Shapes

Three radii: 8px for controls (buttons, inputs, nav items, menu rows), 12px for panels and the KPI strip (menus and toasts 10-12px, palette 14px), 16px for the sheet and sign-in sheet. Pills and the risk rail are fully round; bars use 4px. A 2px dashed cobalt-tinted border marks the CSV drop zone, the only dashed element. Borders are 1px (2px for the focus ring and timeline spine).

## Components

### Buttons
- **Shape:** 8px radius, 36px high (30px `small`, square 36px `icon`), 700 13.5px.
- **Primary:** cobalt fill, white text (dark: #0f1320 on #7d9cf2), padding 0 16px. Hover cobalt-hover. Press nudges down 1px and scales .985. Disabled 50% opacity. One primary per view.
- **Secondary (`plain`):** sheet fill, ink text, 1px control border; hover raised.
- **Ghost:** transparent, muted text; hover raised.
- **Danger:** deliberately the same neutral outline as secondary. Never red.

### Inputs / Fields
- 36px high, sheet fill, 1px control border, 8px radius. Hover darkens the border; focus shows a cobalt border plus a 3px 22% cobalt glow. Placeholder muted; caret cobalt. Labels sit above at 13px/600.

### Animated Select
- Replaces native selects for filters. The trigger reads like a secondary input with a chevron that rotates 180deg. The listbox is fixed-positioned, opens up only when it does not fit below, slides 8px and scaleY(.94) in over 200ms (ease-out) and out over 140ms (ease-in). Active row cobalt tint; selected row cobalt text with a check.

### KPI Strip
- One 12px-radius bordered container divided by 1px lines, not separate tiles. Label muted 12.5px, value 26px/600, note 12px. Each figure is an explain trigger (dotted underline); hover or focus reveals a dark popover with formula, source run and checks. Figures count up over 800ms, once.

### Tables
- No zebra. Header 12px muted/500, rows separated by 1px line, 11px 10px cell padding. Clickable rows tint cobalt on hover/active and support arrow keys + Enter. Fail/warn rows tint risk/warn at 60%. Rows rise in with a 22ms stagger capped at 12 steps.

### Pills
- 22px high, round, 12px/700, optional dot. high/fail/failed/rejected/churned use risk; medium/warn/queued/processing/pending use warn; low/neutral/no_response/inactive use neutral; pass/done/retained/active use ok.

### Bars and Risk Meter
- Bars: 12px tall, cobalt fill on cobalt-tint track, grow from zero over 900ms with a 70ms per-item stagger. Meter: 14px rail; fill red (high), warn-ink (medium), muted (low); grows over 1s.

### Navigation
- Sidebar links 36px, 13.5px/600, 17px icons, on the frame at 82% opacity; hover frame-soft; active frame-active at full opacity. Group labels 12px/600 sentence case. The top bar holds the wordmark, a 260px search trigger with a Ctrl+D hint (the user's choice) and the account menu.

### Account Menu and Theme Switch
- Float-shadowed 12px menu. The theme switch is a three-way segmented control (System, Light, Dark) with a sliding frosted-glass thumb (blur 10px, saturate 1.5). This glass is a user-approved exception.

### Toasts
- Dark ink toast, bottom centre. Reversible actions show Undo and a 3px cobalt timer bar draining over 5s; nothing is written until the window passes. Error toasts use risk-ink.

### Command Palette
- Ctrl+D. Centred 620px, 14px radius, Float shadow over the overlay. 54px input, 40px rows, selected row cobalt tint, footer with key hints.

### Timeline
- 22px round nodes on a 2px line spine: score nodes cobalt, action nodes ok, risk nodes risk, neutral otherwise.

### Sign-in and Hand-off
- Left pane is the apricot frame with a canvas dot-field scene and the Bricolage headline; right is a sheet card. On success the card glides to centre (about 2s in total), a cobalt cross cracks it into 4 pieces, and the workbench sheet opens via `clip-path`; chrome fades in after. Driven by `html[data-handoff]` (see `Handoff.tsx`).

## Motion

Three easings only: `--ease-out` cubic-bezier(0.16, 1, 0.3, 1) for arrivals; `--ease-in` (0.6, 0, 0.9, 0.35) for exits; `--ease-in-out` (0.65, 0, 0.35, 1) for moves between two states. Durations: 110ms fast, 320ms medium, 900ms slow.

- **Stagger rise:** `.rise` children translate 14px and fade over 560ms, 70ms apart (index capped at 5), with `animation-fill-mode: backwards` so no stacking context lingers at rest.
- **Route change:** root view transition, 140ms out (ease-in), 260ms in (ease-out).
- **Shared element:** the customer ID uses a `customer-id` view-transition name between list and customer page, 520ms ease-in-out.
- **Others:** KPI count-up 800ms once; bars and meters scale from zero; toast enters in 320ms and drains over 5s; errors shake for 340ms.
- **Reduced motion:** durations drop to 1ms, view transitions are disabled, content is visible at rest.

Scrollbars are thin, rounded and warm (webkit custom styling; Firefox gets thin `scrollbar-color` only, because setting it in Chromium would override the custom look).

## Do's and Don'ts

### Do:
- **Do** keep red for customer risk only; use neutral outlines for destructive admin buttons.
- **Do** use cobalt for data and exactly one primary action per view.
- **Do** read colours from the CSS custom properties so light and dark both work; add dark values wherever you add a token.
- **Do** keep the sheet and panels flat; use Float only for menus, popovers, toasts and the palette.
- **Do** use Bricolage for titles and the wordmark only; Manrope with tabular figures elsewhere; right-align numbers.
- **Do** show money without a currency symbol and percentages with 2 decimals.
- **Do** use Undo toasts for reversible actions; keep security and admin actions explicit.
- **Do** use the three named easings and respect reduced motion; keep content visible at rest.

### Don't:
- **Don't** add icon-in-circle KPI tiles, sparklines, gradients, eyebrows, uppercase tracked headers, zebra tables or `confirm()` dialogs for reversible actions.
- **Don't** put the accent colour on the frame or chrome.
- **Don't** add resting shadows or glass; the only glass is the theme-switch thumb, by the user's request.
- **Don't** show status by colour alone.
- **Don't** add currency symbols.
- **Don't** use a native `<select>` for filters; use the animated Select.
