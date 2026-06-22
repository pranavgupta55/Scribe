# ADR 0004 — Visual-bug batch (chat clamp · decomp width · panel cap · empties · header)

- **Status:** Accepted
- **Date:** 2026-06-20
- **Related code:** `graph/index.html` (`.query-strip`, `.qs-card`, `.copy-col.new-panel`, `#dev-header`), `graph/graph.js` (`centerChatToViewport`, `buildCopyTurn`)
- **Related ADRs:**
  - ADR 0002 D2 — viewport-centered chat. **Refined** here: the shim now clamps so chat content cannot slide under dev-panel.
  - ADR 0002 D3 — narrow (70 px) decomp strip + per-card outlines. **Refined** here: strip widened to 140 px and cards centered vertically.
  - ADR 0003 — topbar invisibility. **Builds on:** dev-header is given more top padding now that the topbar bar is invisible.

## Context

On 2026-06-20 the user posted a six-screenshot tour of the Scribe local web
host and asked for a UI fix-up. After the topbar-invisibility change landed
in ADR 0003, a grilling session confirmed eight remaining defects across
the chat and copy views:

1. **Chat content clipped on the left** by the dev-panel (images 1, 3, 7).
   `centerChatToViewport()` (graph/graph.js:1044, introduced in ADR 0002
   D2) applies `translateX((rightW − devW) / 2)` to `.chat-inner`,
   `.composer-inner`, and `.composer-meta` to keep them aligned with the
   viewport midline. When `devW > rightW` the shift is negative and large
   — chat-inner slides far enough left that its first ~30–60 px overlap
   the dev-panel. Title characters and bullet markers disappear.
2. **Decomp middle strip is 70 px wide** (images 2, 4, 6). Question text
   wraps to 1–2 characters per line; the `fitQueryStrip()` font-shrink
   bottoms out at 7 px and still overflows. The strip also stretches
   `.qs-card { flex: 1 1 0 }` to fill panel height, distorting card
   geometry when the panel is tall.
3. **NEW vs ALL bento panels have radically different cell sizes**
   (images 2, 4). `.copy-col.new-panel { flex: 3 }` vs
   `.copy-col.all-panel { flex: 2 }` is unconditional. When source counts
   are similar (44 vs 44), the 3:2 split makes NEW cells balloon and ALL
   cells shrink — the same data shown at two very different sizes.
4. **COPY ALL NEW button is clickable when there are zero new sources**
   (image 6). The grid renders `— no new sources —` but the big red
   button below still fires the copy handler with an empty string.
5. **Chat composer slides under dev-panel content** (image 7). Subsumed
   by #1 — same `translateX` shim affects `.composer-inner` and
   `.composer-meta`. Clamping the shift fixes both.
6. **Dev-header (`DEV · LAST ROUND-TRIP`) floats with no visual anchor**
   after ADR 0003 removed the topbar border. The header sits flush
   against the invisible 48-px reserve with only 12 px of top padding.
7. **Off-stage Copy turns visible as faded slivers above/below the
   active stage** — confirmed by the user as **not a bug**: the carousel
   shows adjacent turns intentionally. Skipped.
8. Topbar invisibility — already shipped in ADR 0003.

## Decision

### D1 — `centerChatToViewport()` clamps the shift so content can't overlap

The shim still computes the same desired translation
(`(rightW − devW) / 2`), but now clamps it to the slack that
`.chat-inner` has inside `#chat-main`:

```text
slack = (chatMainW − innerW) / 2
shift = clamp(desired, −slack, +slack)
```

`innerW` is `min(720, chatMainW)` — the chat-inner's actual width (its
`max-width: 720px` cap, bounded by chat-main's width). When the desired
shift exceeds the slack in either direction, content would otherwise slide
past the column edge; clamping reverts to "centered within chat-main" for
that axis.

Net effect: when dev-panel and right sidebar are both visible at similar
widths, viewport-centering still applies (ADR 0002 D2 invariant preserved).
When dev-panel is much wider than the right column (the common case that
caused the bug), the composer/text stay anchored in chat-main instead of
overlapping the dev-panel.

The same shift is applied to `.composer-inner` and `.composer-meta`, so
issue #5 is resolved by this single change.

### D2 — Decomp strip widened to 140 px, cards centered vertically, sized to content

CSS changes to `.query-strip` and `.qs-card`:

- `.query-strip { flex: 0 0 140px; padding: 12px 10px; align-items: center; gap: 8px; }`
  (was `flex: 0 0 70px; padding: 8px 0; no align-items;`)
- `.qs-card { flex: 0 0 auto; width: 100%; padding: 8px 10px; }`
  (was `flex: 1 1 0` — stretched to fill height, distorting cards.
  Now sized to content; the `justify-content: center` already on the strip
  vertically centers the stack.)
- `.qs-text { font-size: 11px; line-height: 1.4; }` — natural reading
  size; `fitQueryStrip()` retained as a defensive shrink for pathological
  long sub-queries (now caps at 12 px, floors at 9 px).

The 70 px taken from the strip's neighbours is reclaimed back into the
NEW / ALL panels via their flex ratios, which slightly narrows them but
not visibly at standard viewport widths.

### D3 — NEW panel capped at `max-width: 900px`; keeps 3:2 flex ratio

Add a single line to `.copy-col.new-panel`:

```css
max-width: 900px;
```

When the natural 3:2 split would give NEW more than 900 px (i.e., on
viewports wider than ~1500 px after subtracting the 140 px strip and
gutters), NEW caps and the leftover width grows ALL via its
`flex: 2 1 0`. Cells in both panels reach approximate parity. On
narrower viewports the cap doesn't engage and behaviour is unchanged.

This was explicitly chosen over auto-flex-by-count parity (which would
have changed behaviour on every turn shape) and over uniform-cell-size
(which would break the bento "see-all-at-once" feel).

### D4a — Hide `COPY ALL NEW` when `newSources.length === 0`

In `buildCopyTurn()` (graph/graph.js:~2017), inside the existing
`newSources.length === 0` branch, set `newCopyAll.style.display = 'none'`.
The placeholder text in the empty grid is enough state communication; the
button reappears on the next turn that does have new sources.

### D4b — Dev-header gains 8 px top padding for visual breathing room

`#dev-header { padding: 20px 16px 12px; }` (was `12px 16px`). The header
no longer sits flush against the now-invisible topbar reserve. No
border / color change — the existing `border-bottom: 1px solid #16162a`
still separates header from scroll content below.

## Consequences

**Positive**
- Chat-empty title and message bullets no longer clipped on the left.
- Composer no longer slides under dev-panel content.
- Decomp question text readable in a single glance (3–4 words per line).
- NEW vs ALL grids visually balanced when source counts match.
- Empty-state of NEW panel reads cleanly — no dead button.
- Dev-header has an obvious resting position under the invisible bar.

**Negative**
- `centerChatToViewport()` is no longer always-viewport-centered. When
  dev-panel is wide and right is narrow, content sits slightly right of
  viewport center — an acceptable trade for "no clipping at all."
- NEW max-width cap is an arbitrary px value (900). If the design later
  prefers larger NEW panels on ultrawide displays, this becomes a
  follow-up tweak. Not blocking.
- Decomp strip cap consumes 70 px that the bento grids used to share.
  Cells shrink by ≈4–5 % on equal-count turns. Acceptable.

## Implementation Plan

Files touched: `graph/index.html` (CSS only) and `graph/graph.js`
(`centerChatToViewport`, `buildCopyTurn`'s zero-source branch).

1. `graph/graph.js:1044` — rewrite `centerChatToViewport()` to clamp the
   shift against `chatMain` slack. Existing call sites (`dev-toggle`,
   `right-toggle`, view-change, `window.resize`) are unchanged.
2. `graph/index.html` (`.query-strip`, `.qs-card`, `.qs-text` rule block,
   ~line 1218) — apply D2 widening + centering + sizing.
3. `graph/index.html` (`.copy-col.new-panel`, ~line 1212) — add
   `max-width: 900px`.
4. `graph/graph.js` (`buildCopyTurn`, ~line 2017 zero-source branch) —
   add `newCopyAll.style.display = 'none';`.
5. `graph/index.html` (`#dev-header`, ~line 691) — bump padding-top to
   20 px.

## Verification

Manual smoke per view:
- **Chat (empty state)**: title "Ask your knowledge base" fully visible;
  subtitle wraps without leading words swallowed; no composer overlap.
- **Chat (with messages, dev-panel open, right open)**: assistant message
  bullets `*` visible at the start of each line; composer sits inside
  chat-main with no dev-panel content showing behind it.
- **Copy (multi sub-query)**: middle strip is ~140 px wide, decomp
  question cards vertically centered with readable 11 px text wrapping
  ~3–4 words per line.
- **Copy (44 vs 44 sources)**: NEW and ALL cell sizes roughly match.
- **Copy (0 new sources)**: `— no new sources —` placeholder visible;
  no red `COPY ALL NEW` button below it.
- **Dev panel**: `DEV · LAST ROUND-TRIP` has visible breathing room
  between it and the topbar reserve.

## Build Log

- 2026-06-20: ADR accepted after grilling session confirmed scope and
  approaches. Implementation pending in the next commit.
