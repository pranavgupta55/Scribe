# ADR 0003 — Topbar invisibility (chrome-less, still load-bearing)

- **Status:** Accepted
- **Date:** 2026-06-20
- **Related code:** `graph/index.html` (`#topbar` block, ~line 70)
- **Related ADRs:** ADR 0002 D1 (three-column topbar grid layout — preserved)

## Context

The unified `#topbar` introduced in ADR 0002 D1 is a fixed 48 px horizontal
bar that hosts the dev-toggle, status pill, model-only toggles, the
GRAPH/CHAT/COPY view switcher, the graph-center button, and the right-
toggle. Functionally correct, but visually it draws a faint hairline across
every view (`border-bottom: 1px solid var(--border-subtle)`) which makes
the top of the page read as "two surfaces stacked" rather than one canvas
with floating chrome.

During the 2026-06-20 visual-bug audit the user asked: "make the topbar
invisible to the user. it should still take up space and contain all of
the buttons, but it shouldn't have a visible border line or anything."

The need is specifically *for the bar surface*, not the controls. The
controls inside (`#dev-toggle`, `#status-pill`, `#view-toggle`,
`#center-btn`, `#right-toggle`, `#gemini-only-btn`, `#qwen-only-btn`) keep
their own card backgrounds and borders so they remain visually anchored
floating chips against the page background.

## Decision

Strip the `#topbar` element of all surface chrome while preserving its
layout role:

1. `background: transparent` (was `var(--bg-base)`). The page background
   shows through; the topbar reads as continuous with the canvas / chat
   / copy stage beneath it.
2. `border-bottom: none` (was `1px solid var(--border-subtle)`).
3. `height: 48px` and `position: fixed` retained, and `body { padding-top:
   48px }` (graph/index.html:62) is left in place so the topbar continues
   to reserve its 48 px slot at the top of the viewport. No content can
   slide under it.

Buttons inside are untouched. The dev-toggle, view-toggle pill, status
pill, center button, mode-only toggles, and right-toggle all keep their
existing `var(--bg-card)` backgrounds and subtle borders. Result: floating
controls on a seamless canvas.

## Consequences

**Positive**
- Single-surface feel across Graph / Chat / Copy views; no horizontal rule
  at y=48 px.
- No layout shift — the 48 px reserve is unchanged so chat composer,
  graph canvas, copy stage, and right sidebar all keep their current
  vertical positions.
- Buttons remain hit-target-clear because they're chips, not bar items.

**Negative**
- Loss of an implicit visual hint that the top region is interactive.
  Mitigated: each control has its own background, hover state, and tooltip.
- The chat dev-header (`DEV · LAST ROUND-TRIP`) and the chat-empty title
  now both sit close to the top with no rule between them and the topbar
  controls. Will be re-evaluated in the broader visual-bug audit (see ADR
  0004 once accepted) — if it reads "floaty" we add a soft inset shadow
  or simply tighten dev-header padding.

## Implementation Plan

Single CSS-only diff in `graph/index.html`, ~line 70 `#topbar` block:

```diff
- background: var(--bg-base);
- border-bottom: 1px solid var(--border-subtle);
+ background: transparent;
+ border-bottom: none;
```

No JS changes. No new files. No new tokens. The change is fully reversible
with a one-line revert.

## Verification

Manual smoke test:
- Open each of Graph / Chat / Copy views; confirm no hairline rule at
  y=48 px against `--bg-base` (#0a0a0a).
- Confirm every control inside the topbar still clickable and still
  visually distinguishable (chip backgrounds intact).
- Confirm graph canvas, chat composer, and copy stage all start at
  y=48 px (no overlap, no extra gap).

## Build Log

- 2026-06-20: ADR accepted. CSS change applied to `graph/index.html`
  `#topbar` rule (background → transparent, border-bottom → none).
  Comment block above the rule extended to document the
  "invisible surface, visible controls" invariant.
