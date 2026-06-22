# 0001: v2 Viewer Adaptation and Source Search

## Status
Accepted

## Date
2026-06-21

## Context

After the Phase-3/4/5 v2 graph rebuild (commit `f7e6e47`, 4865 nodes vs. v1's 3843), the existing viewer (`graph/graph.js` + `graph/index.html`) exposes four coherent defects and one missing capability when fed `graph_v2.json`:

1. **Sidebar shows raw node IDs.** The list renders `n.id` directly (`graph/graph.js:368`), so the right-sidebar TOPICS column reads `claim:0 / claim:1 / claim:10 / claim:100…` for v2 nodes whose IDs are `claim:NNN` / `source:foo.txt` / `concept:Bar`. The v2 schema *does* carry a human label on every node (`n.label`).
2. **Force-directed graph is a hairball.** The render passes all 4865 nodes (1809 L2/L2a/L2b claims + 928 L3 examples + 1039 L4′ practices + 415 L1 frameworks + 369 L0 concepts + 305 sources) to D3 in one shot. Sub-trees of leaf nodes (notably L3 examples grouped under a single concept) end up as a disconnected red sphere floating off-canvas.
3. **Chat markdown renders bullets as literal `*`.** `renderBlocks()` (`graph.js:826`) accepts a block as a `<ul>` only when *every* non-empty line matches `^\s*[-*]\s+` (line 860). Gemini emits mixed numbered + indented sub-bullets (`1. Item\n    * sub`). The mixed block falls to the paragraph branch and the `*` characters survive as text.
4. **Source citations are not clickable.** `buildSourceLabels()` (`graph.js:898`) keys `sourceLabels` by full node ID (`source:foo.txt`) but the chat output references bare filenames (`foo.txt`). The regex built from those keys never matches, so `applySourceShorthand()` is a no-op for v2 source nodes — every `foo.txt` in the answer body stays as inert text.
5. **No way to find a source.** With 305 source nodes the right-sidebar list is unscrollable as a finding tool. A simple typed-query filter would replace scroll-and-skim.

These problems are observed at viewer-load time on the v2 graph and were not pre-existing; they surface because v2's node schema (typed IDs + a sibling `label` field) differs from v1's flat topic/source schema, and because the v2 graph is ~2.8× denser.

## Decision Drivers

- **Must:** Sidebar labels human-readable on v2 graph.
- **Must:** Chat markdown renders mixed numbered/bulleted lists correctly.
- **Must:** Source filename citations in chat and right-sidebar bodies are clickable pills.
- **Should:** Reduce default node count rendered so the layout is legible; allow opt-in to claim/example/practice tiers.
- **Should:** Add a source-search input over the right-sidebar so 305 sources are reachable in <2 seconds.
- **Should not:** Break v1 viewing (still opt-in via `?graph=v1`).
- **Should not:** Re-derive labels server-side — `graph_v2.json` already ships `n.label`. Adapt the client.

## Decision

Adapt the viewer in `graph/graph.js` + `graph/index.html` along five concrete axes (one per defect/feature). No new dependency. No re-export. All changes client-side; the bug surface is the v1→v2 schema gap plus a markdown bullet rule that pre-dates either.

1. **Sidebar uses `n.label` with `n.id` fallback** at the sole render site (line 368).
2. **Level filter** in the right-sidebar above the source list (default: show L0+L1+source). Stored as part of `cfg`, applied in `rebuildSim()`; hidden levels neither render nor participate in physics.
3. **Markdown renderer upgrade** to handle ordered lists (`^\s*\d+\.\s+`), mixed ordered/unordered blocks, and one level of indentation (4-space or tab) for nested `*`/`-` bullets. Implemented inside `renderBlocks()`; same `escapeHtml`-once contract.
4. **Source-label keys stripped of the `source:` prefix** so `applySourceShorthand()` matches bare filenames. The full-id path stays for any caller that resolves by id.
5. **Search input** above the right-sidebar list (lives inside the existing right column header, not the topbar — the user sketched it as a sidebar feature). BM25-lite scoring over `label` (title) and `description` (summary), case-insensitive, tokenized on whitespace + punctuation, with English stopword set. Empty input → existing source list. Non-empty → two grouped sections: **Title matches** (any token hit on label), then **All other matches** (token hit on description only). Render-only; no server round-trip.

## Alternatives Considered

### A. (Chosen) Adapt client viewer + add BM25-lite search in JS
Why chosen: every defect is upstream of rendering and has a fix that doesn't require re-exporting or adding a dependency. BM25-lite over 305 short strings is sub-millisecond in pure JS; no Lunr/MiniSearch needed.
Tradeoffs accepted: hand-rolled scorer (~30 lines) we own forever; markdown renderer accretes more cases (numbered + nesting); we don't expose level-filter as a URL param yet (defer).

### B. Re-export `graph_v2.json` with denormalized labels and pre-filtered visibility tiers
Why rejected: `n.label` already exists — duplicating it as a render-friendly id wastes a field. Pre-filtering on the server (e.g., a `graph_v2_small.json` with only L0+L1+source) means the viewer can't toggle without a refetch; the user has shown they want to drill in interactively. Same export script would still need a "show me everything" path.

### C. Replace the hand-rolled markdown renderer with marked.js or remark
Why rejected: pulls a dependency for a single chat panel. The current renderer already handles inline code, bold, italic, blockquote, heading, hr, lists, and the bespoke source-shorthand pass — the gap is just ordered lists + nesting. Two added rules in `renderBlocks()` close it. The playbook also warns against "reinventing CSS centering with JS"; pulling marked.js to fix two missing list types is the dependency-side analogue.

### D. Add MiniSearch / Lunr for the source search
Why rejected: 305 documents × ~50 tokens average is well under the threshold where a proper inverted index pays off. A naive scorer (term-frequency in label gets 4× weight, in description gets 1×) hits the user's "title-section first, then everything else" requirement directly and stays under ~30 lines.

## Consequences

**Positive:**
- Sidebar becomes legible on the v2 graph; concepts and sources read as themselves.
- Chat answers regain the markdown shape Gemini intends.
- Source citations gain back their clickable-pill behavior, which the user has built tooling around (the Copy view depends on it).
- Search makes the 305-source library navigable without scroll.
- Level filter lets the user lean into the v2 density when they want it, and ignore it when they don't.

**Negative:**
- Two more controls in the right sidebar (search input, level toggle group). Visual budget tighter.
- Markdown renderer grows a numbered-list + nested case. We own that complexity.
- BM25-lite scorer drifts from "real" BM25; rank quality on edge queries (rare terms, long stopword strings) is worse than a library would give.

**Risks:**
- Level-filter side effects on layout: hidden nodes still in `nodes[]` array but skipped at `draw()` / force ticks. Need to confirm D3 doesn't crash on `node.x === null` for skipped nodes (existing code already handles via `if (n.x == null) continue` in `hitTest`).
- Markdown renderer regressions on existing chat content. Mitigation: keep the existing unordered-list branch unchanged; the new ordered-list branch only triggers when the block matches `^\s*\d+\.\s+`. Nested bullets piggyback the existing renderInline pass.
- Search input losing focus when sidebar re-renders. Mitigation: render search bar OUTSIDE the redrawn list region (header), not inside it. Same pattern as the existing `node-list-col` scroll preservation at line 361.

## Implementation Plan

Files touched:
- `graph/graph.js` (sidebar render, sourceLabels builder, renderBlocks, new search + level-filter logic)
- `graph/index.html` (search input markup + level-filter checkboxes in right sidebar header; CSS for both)
- `ADRs/decisions/0001-v2-viewer-adaptation-and-source-search.md` (this file)

Concrete changes:

- [x] **Sidebar label** — `graph/graph.js:368`: render `n.label || n.id` instead of `n.id`. One-line change.
- [x] **Source label keys** — `graph/graph.js:898-915`: in `buildSourceLabels`, strip a leading `source:` from `n.id` before keying `sourceLabels` and emitting into the regex set. Continue to set `sourceLabels[n.id]` AS WELL so any legacy resolver paths still work.
- [x] **Markdown ordered + nested lists** — `graph/graph.js:826-872`: extend `renderBlocks()` to detect `^\s*\d+\.\s+` as ordered-list items and emit `<ol>`; detect mixed ordered + indented `*`/`-` sub-bullets and emit nested `<ul>` inside the parent `<li>`. Add two unit-style smoke checks (commented sample inputs) in the comment block to anchor future regressions.
- [x] **Level filter** — `graph/graph.js`: add `cfg.visibleLevels` (Set of strings, default `new Set(['L0','L1','source'])`); in `draw()`, links iter, force-tick consumers — early-continue when `!cfg.visibleLevels.has(n.level)`. Add a checkbox group in the right-sidebar header (under TOPICS heading). Levels exposed: L0/L1/L2-claims/L3-examples/L4'-practices/source. Clicking a checkbox toggles + calls `rebuildSim()`.
- [x] **Source search bar** — `graph/index.html`: insert `<input id="source-search" placeholder="Search sources…" />` directly above the `#node-list-col` (in the sidebar, NOT the topbar — sketch is sidebar-local). New JS: `tokenize(s)` (lowercase, split on `/[\s\p{P}_]+/u`, drop stopwords); `scoreSource(node, queryTokens)` returns `{titleHits, descHits, score}`; on input, if non-empty, hide the full list, render two `<section>`s: "Title matches" (any token in label) then "All other matches" (token in description only); empty input restores the regular renderTopics path.
- [x] **CSS** — `graph/index.html`: scope new elements under `#right-sidebar` (or existing equivalent). Use existing tokens (`--bg-card`, `--accent-red`, etc.). Sharp corners, monospace numbers (per `0bcf5fd` aesthetic).

Verification checkpoints (per the GUI playbook):
- Run `serve.sh` and HARD-REFRESH (cmd-shift-R).
- TOPICS column shows concept names, source titles, claim text snippets — NOT `claim:NNN`.
- Toggle "L2-claims" off and the big red ball collapses; toggle back on and it reappears.
- Open the chat panel, ask any question; bullets render as bullets, not literal `*`; source filenames render as bold red pills you can click.
- Type "tiktok" into the new search bar — sources whose titles contain "tiktok" appear in the top section, sources whose descriptions mention it (but title doesn't) appear in the lower "All other matches" section; clearing the input restores the full list.
- Confirm no console errors. Confirm right-sidebar scroll position is preserved across re-renders.

## Build Log

<!-- Appended automatically during build. Do not edit entries once written. -->

### 2026-06-21 — Implementation pass

**Change 1 — Sidebar label**
- *problem:* `graph.js:368` rendered `n.id` so v2's `claim:NNN` / `source:foo.txt` showed as raw ids.
- *solution:* Switched to `n.label || n.id` and ran the value through `escapeHtml` to keep the injection surface clean (labels are author-controlled text).
- *tests:* Headless Chromium render of the right sidebar's first 12 entries — all show human prose (`"$1.7M Welding Brand With 300K TikTok Followers Stuck"`, `"$100M Series Stacking Architecture"`, …), none match `^claim:\d+$`.
- *outcome:* TOPICS column is readable on v2.

**Change 2 — Source label keys**
- *problem:* `buildSourceLabels` keyed `sourceLabels` by the full v2 id (`source:foo.txt`). The regex therefore only matched the prefixed form, never the bare `foo.txt` Gemini emits, so `applySourceShorthand` was a no-op on v2.
- *solution:* Strip leading `source:` when keying. Set BOTH `sourceLabels[bare]` (for the regex / chat-shorthand path) and `sourceLabels[fullId]` (for any caller that resolves by full id, e.g. the selected-node title lookup). The regex is built from the bare names. `selectNodeById` was extended to recognise either form so a click on a pill with `data-node="foo.txt"` still finds the `source:foo.txt` node.
- *tests:* Direct renderer harness on canonical input produced `<strong class="src-ref node-link" data-node="foo.txt">Foo</strong>` for the bare filenames. Detail panel on a TikTok source rendered with its mapped short label as the title.
- *outcome:* Source citations gain back clickable-pill behavior on v2.

**Change 3 — Markdown ordered + nested lists**
- *problem:* `renderBlocks` accepted a block as `<ul>` only when every line matched `^\s*[-*]\s+`. Gemini's typical mixed ordered + indented sub-bullet block fell to the paragraph branch; `*` survived as text.
- *solution:* Added `RE_ORDERED = /^(\s*)\d+\.\s+(.*)$/`. New `buildListHtml` decides outer tag from the first matching line, measures `minIndent`, treats anything `> minIndent` as a sub-bullet folded into the most-recent top-level `<li>`. Existing flat unordered path is preserved (it now flows through `buildListHtml` too).
- *tests:* Headless harness on the canonical playbook input produced `<ol><li><strong>Focus on performance-based services</strong> (<strong class="src-ref">Foo</strong>). Examples:<ul><li>…</li><li>…</li></ul></li><li>Leverage your skills (<strong class="src-ref">Baz</strong>).</li></ol>` — exactly the desired shape; no literal `*` survived.
- *outcome:* Mixed numbered + nested bullets render correctly in chat; source filenames inside them become bold red pills via the existing `applySourceShorthand` pass.

**Change 4 — Level filter**
- *problem:* Default v2 viewer rendered all 4865 nodes; the L2/L2a/L2b claims + L3 examples + L4' practices formed a dense red sphere that obscured the L0/L1 structure.
- *solution:* Added `cfg.visibleLevels = new Set(['L0','L1','source'])`. Helper `isVisible(n)` returns true if `n.level == null` (v1 back-compat) or the set contains it. Three points of contact:
  - `forceSimulation(activeNodes())` — sim only knows about visible nodes; hidden nodes keep their `x/y` so re-enabling resumes from last position.
  - `activeLinks()` drops edges with a hidden endpoint.
  - `draw()` skips hidden nodes and edges.
  - `buildNodeList()` filters TOPICS to visible only.
  - `hitTest` ignores hidden nodes.
  Eight checkboxes in the right sidebar header (L0/L1/L2/L2a/L2b/L3/L4'/source) flip set membership and call `rebuildSim()` + `draw()`.
- *tests:* Default `node-item` count = 1089 (matches L0:369 + L1:415 + source:305). Toggling L2 on adds 780 (matches the L2 count in the JSON). Toggling off restores 1089.
- *outcome:* Default view is legible; user can opt into denser tiers without a reload.

**Change 5 — Source search bar**
- *problem:* 305 sources in the right sidebar are unsortable as a finding tool.
- *solution:* `<input id="source-search">` placed inside `#list-header`, which is NOT touched by `buildNodeList`'s re-render — focus survives keystrokes naturally. `tokenize(s)` lowercases, splits on `/[\s\p{P}_]+/u`, drops `SOURCE_STOPWORDS`. `scoreSource(node, tokens)` returns `{titleHits, descHits, score}` with title hits worth 4× description hits. Non-empty input → two sections: `Title matches` (titleHits > 0) then `All other matches` (descHits only). Empty input restores the default sorted list.
- *tests:* Typing `tiktok` produced 4 title-section entries (incl. `"$1.7M Welding Brand With 300K TikTok Followers Stuck"`, `"TikTok Shop Has a Hidden $200M Loophole"`) and 2 description-only entries. `document.activeElement.id === 'source-search'` remained true across all keystrokes. Clearing restored count = 1089.
- *outcome:* 305-source library reachable in <2 keystrokes.

Final verification: serve.sh up on :8765, hard-refresh in headless Chromium, zero console errors, zero pageerrors after the TDZ fix on `sourceSearchInput`.
