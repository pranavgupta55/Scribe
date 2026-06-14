# Scribe — Graph + Chatbot Build Plan

This is the main agent's build plan, covering the graph fixes (task 1) and the
RAG chatbot (new feature). The knowledge-pipeline overhaul (task 2) is planned
separately in `plan.md` by a parallel Opus agent and implemented after.

---

## Task 1 — Graph fixes

### (a) Too many nodes
Root cause is the extraction pipeline emitting 42 vague, overlapping topics per
video. **Fixed by the `plan.md` pipeline overhaul, not the graph code.** No graph
change can reduce node count without changing the data. Noted here for tracking.

### (b) Nodes won't shrink / link distance maxes too low / text too big
- `nodeRadius = nodeSize + sqrt(degree)*1.8` — the degree term dominates, so the
  "Node size" slider barely moves big nodes. Reduce multiplier `1.8 → 0.7` and
  lower the slider min to `1`.
- Raise "Link dist" slider max `200 → 600` and "Repulsion" max `800 → 1500` so
  the cluster can actually spread.
- Shrink node labels `12px → 9px` (in `graph.js draw()`).

### (c) Source list icon is blue but the source node is purple
`renderLegend()` colors the swatch with the per-source PALETTE (blue). Override
the swatch to the source-hub purple (`STANDALONE_HUB = #7c6af7`) so the legend
matches the actual source node.

### (d) Two identical description sections
The detail panel shows `description` (truncated to 200 chars in `export_graph.py`)
**and** a "Knowledge Note" `summary` (first 2000 chars) — same text twice.
- `export_graph.py`: set `description` to the **full** note body; drop `summary`.
- `index.html`: remove the `#detail-summary-wrap` block + its resize handle CSS.
- `graph.js`: drop summary handling in `selectNode()`; render the description as
  light markdown (bold, bullets, line breaks) so structured notes look right and
  nothing is cut off.

---

## New feature — RAG chatbot ("Graph | Chat" toggle)

### Backend — `server.py` (replaces the static `http.server` in `serve.sh`)
A stdlib `http.server` that:
1. Serves static files (graph viewer) exactly like before.
2. Handles `POST /api/chat` as **Server-Sent Events** so the UI can show
   retrieval happening live:
   - Embed the query with `nomic-embed-text`.
   - Query ChromaDB `facts` (n=8) → collect each fact's `topics` metadata,
     map to graph node ids (`slug → Title Case`) → emit a `nodes` SSE event
     (the "which nodes it accessed" trace, clickable in the UI).
   - Query `chunks` (n=5) for full-context passages → build the grounding context.
   - Stream `qwen3:1.7b` tokens as `token` SSE events; close with `done`.
   - Graceful `error` event if Ollama/Chroma unavailable.
- System prompt: answer **only** from the provided context; cite source files;
  say "not in my knowledge base" when unsupported.

### Frontend — `index.html` + `graph.js`
- **Toggle**: a segmented `Graph | Chat` control at top-right, immediately left of
  the Center button, with a sliding highlight pill (CSS transform transition).
- **Panel swap**: `#left` gains a hidden `#chat-panel`. Chat mode hides the
  canvas / graph-controls / center button and shows the chat panel; Graph mode
  reverses it. The right sidebar (topic list + detail) stays in both modes.
- **Chat UI** (Claude-browser style): scrollable message stream, user + assistant
  bubbles, a rounded auto-growing input with send button, empty-state prompt.
- **Live retrieval trace**: each assistant turn first renders a "Searching
  knowledge base…" row that resolves into clickable node chips as the `nodes`
  event arrives; then the answer streams in token-by-token.
- **Clickable nodes**: chips/inline node titles call the existing `selectNode()`
  (same module scope) to open that topic in the right detail sidebar.

### `serve.sh`
Swap `python3 -m http.server` for `python3 server.py` (same port 8765).

---

## Order of work
1. Graph fixes (b), (c), (d) — independent of pipeline. ✅ do now.
2. Chatbot backend + frontend — independent of pipeline (rides on the existing
   `chunks`/`facts` collections, which survive the overhaul). ✅ do now.
3. After `plan.md` lands: implement the pipeline overhaul, which resolves (a) and
   makes the notes/descriptions tight and grounded.
