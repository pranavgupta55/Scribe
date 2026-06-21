# ADR 0002 — UI polish + gibberish-query gate

- **Status:** Accepted
- **Date:** 2026-06-20
- **Related code:** `server.py`, `graph/index.html`, `graph/graph.js`
- **Supersedes UI parts of:** ADR 0001 (`#topbar` group structure, overlay strategy, system-prompt length)

## Context

Post-ADR-0001 the chat / copy views still had several visible UX defects
plus one substantive backend regression:

1. The 36 px topbar covered the first row of the topics sidebar (`#right`).
2. The card-grouping overlay layer never aligned cleanly with the bento
   placements (z-index races, sub-pixel drift, lag on zoom).
3. The system prompt was longer than the user wanted (~900 t) and the
   token-counter implementation in the composer fought the original
   single-row textarea.
4. The view-toggle (GRAPH / CHAT / COPY) was anchored on the right edge of
   the topbar — not centered.
5. When dev-panel collapses but the right sidebar is open, the chat text
   column slides off-viewport-center.
6. Gibberish queries ("asldkjas dlksajd…") still drove a full embedding
   search and surfaced 40+ noise sources in both chat and copy views.

These are all individually trivial but collectively give the UI a
"prototype" feel — exactly the friction the user was hitting.

## Decision

### D1 — Three-column topbar (`grid-template-columns: 1fr auto 1fr`)

The topbar now uses CSS grid with three explicit slots:

- **Left** (chat-only): `#dev-toggle`, `#status-pill`, `#gemini-only-btn`,
  `#qwen-only-btn`. Plus `#center-btn` (graph-only, sitting alongside).
- **Center**: `#view-toggle` only. `justify-self: center` so it stays
  pinned to the viewport midline regardless of how much content sits in
  the left or right slot.
- **Right**: `#right-toggle`.

Per-view visibility is CSS-driven via `body[data-view=...]` selectors;
the JS only stamps the data attribute. Topbar bumped 36 → 48 px;
controls grew (12 px font, 6 / 12-14 px padding) for clickability.

### D2 — Chat content centered to **viewport**, not just `#chat-main`

Previously `chat-inner` used `margin: 0 auto` which centered it within
the variable-width `#chat-main` flex column. When dev-panel collapses,
`#chat-main` expands left and the text column slides off-center
relative to the viewport.

Fix: a small JS shim `centerChatToViewport()` reads the current visible
widths of `#dev-panel` and `#right`, then applies
`translateX((rightW - devW) / 2)` to `.chat-inner`, `.composer-inner`,
and `.composer-meta`. Net effect: the inner center always sits at
`bodyW / 2`. Hooked into the dev-toggle, right-toggle, view-change, and
window-resize events.

Verified via DevTools-Protocol inspection: with dev collapsed and
right sidebar open, `innerC == bodyW / 2 == 960` on a 1920-wide
viewport.

### D3 — Strip narrower (70 px) + thicker borders, per-card outlines

Replaced the grid-child overlay layer entirely (ADR 0001 D4 supersedes):

- `.query-strip` is `flex: 0 0 70 px`, `justify-content: center`. The
  cards each have a **2 px** border. Their numbered tag is bumped to
  11 px so the strip stays readable at the narrower width.
- The colored cell-outline logic moved to `applyCardColors()` —
  per-card `box-shadow: inset 0 0 0 1.5 px <color>aa` on each bento
  card by `src.primary_query_idx`. No layout cost, no z-index, no
  observer needed.
- Overall `.copy-turn` width shrunk from `100% - 32 px` to
  `100% - 160 px` so the bento grids never crowd the page-stack arrows
  (`▲ / ▼`) pinned to the viewport right edge.

### D4 — Composer reverted; token-only counter below the box

The previous round wrapped the textarea + send button in a vertical
flex and put the counter inside the box. User wanted the original
single-row composer back, with the counter outside.

- `.composer-inner` reverts to `display: flex; align-items: flex-end;
  gap: 8 px` — textarea + send button in one row.
- `.composer-meta` is a sibling **below** `.composer-inner`, in the
  composer's outer flex column. It shows `${t} tokens` only — no char
  count. Warn/alert color tiers at 2 k / 6 k tokens.

### D5 — Strict gibberish gate

`_is_trivial()` now requires (1) at least one word matching a small
English-anchor set (question words, pronouns, articles, common verbs)
AND (2) at least 3 word-tokens total. Anything failing both gets
short-circuited the same way greetings already were — no retrieval,
no source spam, model still answers conversationally.

Verified:

| query | trivial? |
|---|---|
| "hello" | True |
| "asldkjas dlksajd lksajd salkdjsa dlkasj d" | True |
| "alex urgency" | True (2 words, no anchor) |
| "foo bar baz" | True (3 words but no anchor) |
| "what is alex saying about pricing?" | False |
| "Tell me about Alex Hormozi business strategy" | False |

Intentional: bare keyword searches like "alex urgency" route through
the graph view (or rephrased into a question), not the embedding
search. The latter happily matches anything cosine-close, which is
what produced the original 50-source noise dumps.

### D6 — Headless screenshot loop for verification

Added `?view=` and `?cq=` query-params so a headless Chrome can deep-link
into chat / copy mode and auto-submit a copy query. Used during this
ADR's work via `--screenshot=` and Chrome DevTools Protocol via
`--remote-debugging-port=` to inspect computed dimensions and confirm
centering / strip sizing without bouncing screenshots off the user.

## Consequences

**Positive:**

- View-toggle is exactly viewport-centered, not slot-relative; doesn't
  drift when left-side button count changes.
- Chat content stays at viewport center regardless of sidebar state.
- "Hello" no longer triggers retrieval. Gibberish no longer dumps 40
  random sources.
- Composer is back to the original feel (textarea + send inline) plus a
  passive token counter below.

**Negative / open:**

- Strict gibberish gate also catches 2-word keyword searches like
  "alex urgency". This is a deliberate trade — see D5. If false-negatives
  bite, loosen the `len(toks) >= 3` rule to `>= 2`.
- The center-to-viewport shim is JS-driven and only fires on the
  hookable events (toggle clicks, view changes, resize). If something
  programmatically changes sidebar widths without firing one of those,
  the shim won't re-run. Currently nothing does — but a `MutationObserver`
  would be the bulletproof alternative.
- `_ENGLISH_ANCHORS` is in-source, not configurable. Fine for the
  current single-user setup; if Scribe ever ships multi-language
  transcripts, swap to a langdetect-based gate.

## Follow-ups (open)

1. Real tokenizer for the composer counter (currently `chars/3.5`).
2. `MutationObserver` on `#dev-panel` / `#right` so the centering shim
   re-runs without explicit JS hooks.
3. Consider exposing the gibberish heuristic as a `/api/classify`
   endpoint so the copy view can pre-validate before round-tripping.
