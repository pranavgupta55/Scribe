# ADR 0009 — Graph-Augmented RAG for Scribe Chatbot

- **Status:** Accepted
- **Date:** 2026-07-26
- **Authors:** pgupta@kisotechnology.com
- **Related code:** `server.py`, `scripts/rebuild_chroma_v2.py`, `scripts/rebuild_chroma_v2_resume_chunks.py`, `scripts/retrieval_v2.py`, `scripts/prompt_v2.py`, `scripts/eval_v1_vs_v2.py`, [`docs/RAG_V2.md`](../../docs/RAG_V2.md)
- **Related tasks:** t-pprrm1 (rebuild), t-fokp1z (Phase 6 eval, deferred)

---

## Context

Scribe's v1 chatbot retrieval is a flat cosine search over two ChromaDB
collections (`facts`, `chunks`) embedded with `nomic-embed-text` (768-d).
It answers queries by picking the top-K chunks and facts by vector similarity
and feeding them into a plain-text prompt. This works for single-topic
questions where the most relevant chunks land near the query in embedding
space. It fails in two important ways:

First, the `.scribe-skills/` phase pipeline has produced `graph/graph_v2.json`
— a knowledge graph with 5477 nodes and 19154 typed edges linking claim nodes
to each other and to concept/example/practice nodes. The edge types carry
semantic signal that flat cosine cannot see: a `contradiction` edge (weight
1.0) means two claims directly conflict; an `agreement` edge (0.95) means
they reinforce each other; a `builds-on` edge (0.8) means one claim depends
on the other. The v1 retrieval path is oblivious to this structure even though
the underlying data has it.

Second, the v1 prompt template does not distinguish edge types, so
contradictions are silently averaged into the answer rather than surfaced.
This is especially bad for the Hormozi/Ravikant/Ericvelch corpus, which
contains genuine speaker disagreements that a user would want to know about
(e.g. "raise prices" vs "add a guarantee first").

The goal of v2 is to bridge the ChromaDB vector store to the graph so that
each retrieved fact brings its typed neighbors into the prompt context, and
to enforce a hard rule that contradictions are surfaced before the answer
body — not hidden.

The scope constraint is deliberate: v2 covers the 917 sources already in
v1 only. Processing the ~379 new transcripts that have been ingested since
the graph was built is deferred; they require a new graph phase run first.

---

## Decision

- **Rebuild ChromaDB in v2 schema** keyed to `graph_v2.json` node IDs.
  Two new collections: `facts_v2` (one entry per merged claim, ID =
  `claim:{N}`) and `chunks_v2` (same chunks as v1, re-embedded in
  `qwen3-embedding:8b` 4096-d space to match `facts_v2`). The v1
  collections (`facts`, `chunks`, `nomic-embed-text`) are kept in a
  timestamped backup directory; the v1 Atlas endpoints are unaffected.

- **Expand each retrieved fact via graph edges** with kind-specific caps
  (contradicts: 5, agrees: 2, builds-on: 3, hosts: 2, illustrates: 2,
  practices: 10, related: 0). No forced multi-hop; best effort only.

- **Chunk side-effect expansion at query time.** For each expanded
  neighbor, embed its text and query `chunks_v2` (top-1). If cosine
  similarity >= 0.5, add the chunk. This defers `claim_ids[]`
  precomputation from build time to ~10–20ms per neighbor at query time.

- **Dedup in two passes:** (a) union by node ID, keeping highest-weight
  kind; (b) text cosine similarity >= 0.95 within each kind, keeping
  higher-weight member.

- **Anthropic-canonical XML prompt format** with labeled semantic-edge
  tags (`<contradicts>`, `<agrees>`, `<builds_on>`, `<hosts>`,
  `<illustrates>`, `<practices>`). Emission order is fixed:
  contradicts → agrees → builds_on → hosts → illustrates → practices.

- **Enforce contradiction surfacing.** `SYSTEM_PROMPT_V2` contains a hard
  rule: if any `<contradicts>` tag appears in the retrieved context, the
  model must emit a `<conflicts>` block first before any answer content.
  Contradictions may never be dropped during token budgeting.

- **Gate v2 behind a body flag on the existing `/api/chat` endpoint.**
  `use_v2: true` in the POST body activates `retrieval_v2.retrieve_v2()`
  and `prompt_v2.assemble_prompt()`. When absent or false, the v1 flow
  is identical to today. No new endpoint; no client migration required
  to preserve v1 behavior.

- **Primary generation model:** Gemini 2.5 Flash, falling back to
  Gemini 2.0 Flash, then local `qwen3:1.7b`. `generate_stream()` and
  `qwen_stream()` accept an optional `system=` kwarg so v2 can inject
  `SYSTEM_PROMPT_V2` without touching the v1 `_SYSTEM` constant.

- **Query decomposition:** reuse the existing `qwen3:1.7b` decompose
  step from v1, time-budgeted to 2 seconds with Gemini fallback.
  Identical `_DECOMPOSE_SYSTEM` prompt copied verbatim so behavior matches.

---

## Alternatives Considered

### A1. ChromaDB rebuild path

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) Rebuild in v2 schema** ✓ | Wipe v1 `.chroma/`, create `facts_v2` + `chunks_v2`. Clean vector space; allows direct cosine joins between facts and chunks. | **Chosen.** |
| (B) Bridge table | Keep v1 collections; add a side table mapping v1 IDs to graph node IDs at query time. | Rejected. v1 is embedded in a different 768-d space; cross-collection cosine joins are meaningless without re-embedding. |
| (C) Query-time graph bridge | Keep v1; at query time, look up each retrieved chunk's source in the graph and walk edges. | Rejected. Chunks are not keyed to graph nodes; the mapping is lossy and brittle. |

### A2. Chunk expansion method

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) Runtime cosine lookup** ✓ | At query time, embed each expanded neighbor's text and find the nearest chunk in `chunks_v2`. | **Chosen.** Simpler build; ~10–20ms/neighbor is acceptable latency. |
| (B) Precomputed `claim_ids[]` in chunk metadata | At build time, for each chunk, find all claim nodes it contains and store the IDs. | Deferred. Requires an extra build pass (embed all 6055 claims, query chunks_v2 for each). Unlocks O(1) chunk lookup at query time. Worth revisiting if query-time latency becomes a concern. |
| (C) Same-source adjacency | For each expanded neighbor, include all chunks from the same source file. | Rejected. Too coarse; same-source chunks often cover unrelated topics. |
| (D) Skip chunk side-effect entirely | Only expand via facts. | Rejected. Chunks carry transcript context (examples, tone, phrasing) that the graph does not; side-effects are meaningfully different from direct chunk retrieval. |

### A3. Kind allocation (per-fact expansion caps)

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) User-defined, contradiction-heavy** ✓ | contradicts:5, agrees:2, builds-on:3, hosts:2, illustrates:2, practices:10 | **Chosen.** Surfaces genuine disagreements first; practices get a high cap because the corpus has many actionable how-to nodes. |
| (B) Uniform 3 per kind | Equal budget across all kinds. | Rejected. Contradiction budget of 3 is too low for some topics; practices budget of 3 drops high-value how-to content. |
| (C) Proportional to edge count | Allocate caps proportional to the number of edges of each kind in the graph. | Rejected. `related` has 9709 edges — proportional allocation would flood the context with low-signal neighbors. |
| (D) Dynamic per-query tuning | Adjust caps based on how many neighbors each fact actually has. | Deferred. Requires eval data to tune; adds complexity. |

### A4. Edge kind labels in the prompt

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) Explicit labeled XML tags** ✓ | `<contradicts>`, `<agrees>`, etc. as first-class XML elements with semantic attributes. | **Chosen.** Model can follow the contradictions-first rule reliably; labels survive chunked streaming. |
| (B) Inline markers | `[CONTRADICTS]`, `[AGREES]` inline in the fact text. | Rejected. Harder to parse; ambiguous if the claim text itself contains bracket notation. |
| (C) Unlabeled context block | Just include neighbor text without structural labels. | Rejected. Model cannot distinguish contradictions from agreements without labels; the hard surfacing rule would be unenforceable. |
| (D) Label only high-signal kinds | Only label contradictions; lump others under "related context". | Rejected. The distinction between agreements (builds confidence) and builds-on (prerequisite chain) is lost; the model cannot cite the right relationship. |

### A5. Query decomposition

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) Keep `qwen3:1.7b` local with Gemini fallback** ✓ | Matches v1 behavior exactly; 2-second budget. | **Chosen.** Zero latency regression vs v1 for decomposition step. |
| (B) Drop decomposition | Always treat query as a single sub-query. | Rejected. Multi-topic questions degrade significantly without decomposition. |
| (C) Upgrade to Haiku | Replace `qwen3:1.7b` with Claude Haiku for decomposition. | Rejected. Adds API cost and external latency for a step that already works well locally. |

### A6. Reranker

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) No reranker** ✓ | Retrieval ordered by cosine similarity + graph expansion only. | **Chosen (for now).** No eval data yet to measure reranker lift; adds latency and complexity. |
| (B) BGE-reranker-v2-m3 local | Cross-encoder rerank of the combined fact + chunk set before prompt assembly. | Deferred. Likely to improve precision; needs Phase 6 eval data to justify. |
| (C) LLM reranker | Ask Gemini/Claude to score relevance for each retrieved item. | Rejected for v2. Adds 1–3 seconds of latency and significant token cost per query. |

### A7. Answer generation model

| Option | Description | Decision |
|--------|-------------|----------|
| **(A) Gemini chain** ✓ | Gemini 2.5 Flash → 2.0 Flash → local qwen3:1.7b. | **Chosen.** Consistent with v1; no model migration. |
| (B) Switch to Claude | Use Claude Sonnet/Haiku as primary. | Rejected for v2. Would require billing changes and a new API client in `server.py`. Revisit if Gemini quota becomes a persistent problem. |
| (C) Dual-mode | Allow the client to pick Gemini or Claude via a body flag. | Deferred. Adds complexity; wait for v2 baseline quality data first. |

---

## Consequences

### Positive

- Retrieval surfaces typed relationships (especially contradictions) that the
  v1 flat cosine path ignores entirely.
- Contradictions are hard-surfaced before the answer body — the model
  cannot silently average them away.
- v1 stays fully intact behind a flag; zero risk to the Atlas production
  retrieval path.
- The `facts_v2` + `chunks_v2` collections are keyed to stable `claim:N`
  IDs from `graph_v2.json`, enabling future graph-side improvements
  (better edge weights, new edge kinds) to propagate without a schema change.
- `compute_prompt_stats()` + `retrieval_meta` in the `debug` SSE event give
  a clear observability surface for tuning.
- `eval_v1_vs_v2.py` enables a real eval loop for comparing retrieval quality.

### Negative

- The build phase has more failure modes than v1 (ChromaDB SQLite handle
  caching, llama-server silent hangs on long inputs). Both are documented and
  mitigated, but operators need to know the resume story
  (`rebuild_chroma_v2_resume_chunks.py --resume`).
- `chunks_v2` embedding is ~10× slower per call than `nomic-embed-text`
  (`qwen3-embedding:8b` at batch=2 via HTTP vs nomic at batch=16 via
  ollama-python). Total rebuild: 45–60 minutes. Ongoing incremental adds
  will also be slower per chunk.
- Chunk side-effect expansion adds ~10–20ms per expanded neighbor at query
  time (one embed + one ChromaDB query). For a typical query with 3 retrieved
  facts × ~5 neighbors each = ~15 cosine queries. Expected added latency:
  150–300ms at P50.
- Prompt tokens increase by ~5–7k per query (expansion context). This raises
  Gemini token costs proportionally and may occasionally push long
  conversation histories into the compaction zone earlier than v1.

### Neutral

- Opens up a concrete eval loop for tuning `MAX_CONTRADICTS`,
  `SIM_DEDUP_THRESHOLD`, `SIDE_EFFECT_SIM_THRESHOLD`, and per-kind caps
  via `eval_v1_vs_v2.py` once Phase 6 is run.
- The `qwen3-embedding:8b` 4096-d space is a superset of the 768-d
  `nomic-embed-text` space. Cross-collection similarity (v1 chunks vs v2
  facts) is not meaningful; the two systems are intentionally isolated.
