# ADR 0005 — Visual-bug batch (round 2): drop chat shim · transform-immune bento · proportional strip · overflow

- **Status:** Accepted
- **Date:** 2026-06-21
- **Related code:** `graph/index.html` (`#dev-header`, `.query-strip`, `.copy-col`, `.bento-card`, `.copy-turn`), `graph/graph.js` (`centerChatToViewport`, `layoutBentoFor`, `layoutCopyStack`)
- **Supersedes:** ADR 0002 D2 (viewport-centered chat), ADR 0004 D1 (clamp shift), ADR 0004 D3 (3:2 NEW vs ALL flex).

## Context

After ADR 0004 shipped, the user ran `serve.sh` and reported the live
behaviour. Five distinct defects:

1. **Chat content + composer don't center in the leftover space between
   sidebars.** The clamp added in ADR 0004 D1 pins `.chat-inner` to
   chat-main's left edge (not its center) whenever `(rightW − devW) / 2`
   exceeds the chat-inner slack. The math clamps to the *bound*, not the
   *target*. End state: dev-panel visible, right-sidebar collapsed,
   chat text appears left-aligned at the dev-panel boundary.
2. **Dev panel header reads "DEV · LAST ROUND-TRIP" — too verbose.**
   The user wants just "Dev".
3. **Copy bento panels shrink after carousel navigation, recover on
   browser zoom.** Root cause: `layoutBentoFor()` measures
   `col.getBoundingClientRect().width`. Col is inside `.copy-turn`, which
   has `transition: transform 0.32s` on focus changes (translate / scale).
   The double `requestAnimationFrame` waits one paint, not the 320 ms
   transition end, so the rect is sampled mid-animation while
   `scale(<1)` is still partially applied. A subsequent browser zoom
   fires a ResizeObserver after the transition completes, which
   re-measures correctly.
4. **`.query-strip` doesn't scale with viewport.** `flex: 0 0 140px` is
   px-fixed, so when the user zooms, the strip stays the same CSS-px
   width while the flex panels grow/shrink — visually the strip "shrinks
   on zoom-out, grows on zoom-in" relative to its neighbours.
5. **Decomp strip is not at the horizontal centre of `.copy-turn`.** The
   3:2 split between NEW and ALL puts the strip at the 50 % + (3-2)/(2×5)
   = 60 % mark, offset right. The user wants the strip centred.
6. **Bento text too large.** `bc-head` 9.5 px, `bc-body` 8 px. Wants
   smaller for higher information density.
7. **Source count overflow.** With many retrieved sources the bento
   packer chooses a grid that overflows its column vertically; cards
   spill off the bottom edge of the panel.

The earlier process failure deserves an honest note: the ADR 0004 clamp
was implemented from screenshots alone without running `serve.sh`. The
math chose the bound instead of the target, and there was no live
verification step to catch it. ADR 0005 adds verification as a hard
checkpoint in the Implementation Plan.

## Decision

### D1 — Remove `centerChatToViewport()` shim entirely; rely on CSS

`.chat-inner` and `.composer-inner` already have `max-width: 720px`
and `margin: 0 auto`. Inside `#chat-main { flex: 1 }` they center
naturally. When dev-panel or right-sidebar collapse, chat-main resizes
and the centered inner "slides over" automatically — no JS needed.

The function is kept as a no-op that **clears any leftover inline
`transform`** so an old, stale value from a prior code path can't
persist. Call sites remain untouched (they just clear).

Supersedes ADR 0002 D2 and ADR 0004 D1. The "viewport-centered" invariant
they encoded was wrong: the user actually wants "leftover-space-centered,"
which is exactly the natural CSS behaviour.

### D2 — Dev header text: "Dev · last round-trip" → "Dev"

Single text edit in `graph/index.html`. Padding from ADR 0004 D4b
(20 / 16 / 12 px) stays.

### D3 — Bento layout measures via `offsetWidth` / `offsetHeight`, not `getBoundingClientRect()`

`Element.clientWidth` and `Element.offsetWidth` return the layout-box
dimensions, **unaffected by CSS `transform`**. Only `getBoundingClientRect()`
includes transforms. Switching to `col.offsetWidth` / `col.offsetHeight`
makes `layoutBentoFor()` immune to the `.copy-turn` carousel transition
— the same code path produces the same numbers whether or not the
turn is mid-scale.

Header / button height already used `offsetHeight`. The fix is a
two-line change.

### D4 — `.query-strip` becomes proportional: `flex: 1 1 0; min-width: 140px; max-width: 200px`

The strip joins the flex sharing with NEW and ALL. Bounds:

- `min-width: 140px` — text remains readable on narrow viewports.
- `max-width: 200px` — strip doesn't dominate on wide ones.

Within those bounds the strip scales proportionally with the panels
under viewport / zoom changes.

### D5 — NEW vs ALL become equal flex (1:1), NEW keeps 900 px max-width cap

The strip can only be horizontally centred between the panels if the
panels themselves are equal-width. ADR 0004 D3 chose `flex: 3 / flex: 2`
to give NEW visual priority; the user now prefers a centred strip over
the priority weight. NEW retains `max-width: 900px` so it doesn't balloon
on ultrawide viewports.

Supersedes ADR 0004 D3.

### D6 — Bento text shrinks one step

- `.bc-head`: `font-size: 9.5 → 8.5px`.
- `.bc-body`: `font-size: 8 → 7px`, `line-height: 1.4 → 1.35`.
- `fitTextToBox()` floor lowered from 4 → 3 to match.

Counters the density loss from equal-flex panels at standard widths.

### D7 — Source-count overflow: floor cell size + bento overflow scroll

In `layoutBentoFor()`, after computing `cellSize`, if it would be below
the minimum readable size (40 px) clamp it to 60 px and add
`overflow-y: auto` to the col. The grid scrolls inside the col rather
than spilling past the panel's bottom edge.

`.copy-col { overflow-y: auto; scrollbar-width: thin; scrollbar-color:
#2a2a2a transparent }` — applied unconditionally but only triggers when
content overflows.

## Consequences

**Positive**
- Chat content and composer center naturally in `#chat-main`; sidebars
  open/close, content "slides over" by virtue of layout.
- Copy bento cells stay the same size before and after carousel
  navigation; no more zoom-to-reset workaround.
- Strip scales with viewport / zoom, and sits at the horizontal centre
  of `.copy-turn`.
- Dense source counts (60+ items) stay inside their panel instead of
  bleeding off the page.
- The `ADR-implement-without-verifying` failure mode is closed: the
  Implementation Plan now lists "run `serve.sh` and visually verify"
  as the final mandatory step.

**Negative**
- ADR 0002 D2's "viewport-centered" property is gone. The chat column
  centers in chat-main only. Acceptable: the user explicitly preferred
  this.
- NEW and ALL are the same width regardless of content emphasis. Loss
  of visual hierarchy for "new this turn" — mitigated by NEW still
  appearing on the left (read order priority).
- Smaller bento text approaches the lower bound of readability. If users
  complain we revert D6 only.

**Risks**
- `offsetWidth` returns 0 if the element is `display: none`. Code path
  must guard. The `if (availW < 50 || availH < 50) return;` check
  already in place catches this.

## Implementation Plan

Files: `graph/index.html`, `graph/graph.js`.

- [ ] D1: stub `centerChatToViewport()` to clear inline transforms only.
- [ ] D2: edit `#dev-header` inner text in `graph/index.html`.
- [ ] D3: change `col.getBoundingClientRect().width/height` →
      `col.offsetWidth / col.offsetHeight` in `applyPanel()`.
- [ ] D4: `.query-strip { flex: 1 1 0; min-width: 140px; max-width: 200px }`.
- [ ] D5: `.copy-col.new-panel { flex: 1; max-width: 900px }`,
      `.copy-col.all-panel { flex: 1 }`.
- [ ] D6: `.bento-card .bc-head { font-size: 8.5px }`,
      `.bento-card .bc-body { font-size: 7px; line-height: 1.35 }`.
- [ ] D7: in `applyPanel()`, clamp `cellSize` floor; toggle
      `overflow-y: auto` on the col if grid taller than available.
- [ ] Commit, push to `origin/main`.
- [ ] **Verification (mandatory):** run `bash serve.sh`. Open Chat with
      messages; verify text + bullets fully visible, composer centred
      in chat-main. Open Copy; submit a query that decomposes into 3+
      sub-queries; click ▲/▼ — bento cells must NOT shrink. Resize the
      browser; strip width tracks viewport. Submit a query that returns
      60+ sources; verify panel scrolls inside instead of spilling.

## Build Log

- 2026-06-21: ADR accepted after a grilling round that confirmed the
  defect list and root-cause analysis. Implementation in the following
  commit on `main`.
