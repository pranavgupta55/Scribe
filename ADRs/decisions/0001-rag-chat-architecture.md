# ADR 0001 — RAG chat architecture: prompt expansion, history compaction, adaptive retrieval

- **Status:** Accepted (#1, #2 implemented). Adaptive retrieval (#3) documented, deferred.
- **Date:** 2026-06-20
- **Authors:** pgupta@kisotechnology.com
- **Related code:** `server.py`, `graph/graph.js`, `graph/index.html`

## Context

Scribe's RAG chat had four observable failures:

1. **False refusals.** The ~140-token system prompt told the model to refuse if the answer wasn't verbatim in retrieved passages. Result: the chat said "I don't have that in my knowledge base" for queries where adjacent context existed (e.g. "Alex's thoughts on creating urgency" returned a refusal despite 8 consulted topics with strong overlap).
2. **No conversational memory.** Each turn was independent — the model never saw past user messages or prior answers, so follow-ups like "what about team building?" landed without context.
3. **Source thrash.** With no memory, the same sources were re-injected on every turn, wasting tokens and degrading retrieval diversity.
4. **No depth control.** Retrieval was fixed at N_FACTS=10 / N_CHUNKS=5 regardless of question complexity. Multi-faceted questions got the same depth as one-line lookups.

The fixes are non-trivial; we researched the design space with three concurrent Sonnet web-search agents (RAG prompting best practices, conversational RAG history management, adaptive/agentic RAG) and synthesized their recommendations into the decisions below.

## Decision

### D1 — Expand the system prompt (~140t → ~900t)

Replace the terse 4-rule prompt with an XML-sectioned prompt covering:

- `<retrieval_grounded_behavior>` — four rules: anchor every claim, USE related context (don't refuse on tangential), synthesize freely, prioritize usefulness over purity.
- `<citation_rules>` — inline filenames as `(file.txt)`, topic links as `[[topic]]`, one citation per claim, never invent source names.
- `<synthesis_vs_quoting>` — synthesis is default; quote only when wording matters.
- `<uncertainty_and_inference>` — four-tier confidence ladder (directly supported / partially / synthesized / weak) so the model can answer at each tier with appropriate hedging.
- `<when_to_decline>` — two-condition gate (zero semantic overlap AND no partial answer possible). HIGH BAR. Refusal as last resort.
- `<tone_and_format>` — direct prose, no preambles, no separate "Sources" footer.

The four-tier confidence ladder is the load-bearing change — it gives the model explicit permission to answer at each support level instead of binary (cite-or-refuse). This directly fixes the false-refusal regression.

### D2 — Conversational history with tiered token-budget compaction

Server (`server.py`):

- `/api/chat` now accepts `history: [{role, content, sources?}]` from the client.
- `retrieve(query, seen_sources)` filters out sources already attached in prior turns. Per-turn dedup, NOT per-passage — once a source has been seen, the model has it via earlier assistant prose; only fresh sources need re-injection.
- `build_chat_entries(history, query, context)` applies a two-tier eviction policy:
  - **Tier 1 (>24k / 75%):** compress oldest assistant responses to ~400-char truncations. User messages preserved verbatim — their intent signal is small and irreplaceable.
  - **Tier 2 (>32k / 100%):** drop oldest entire turns from the wire context. Most-recent exchange + current question kept at minimum.
- The current question (with this-turn context block) is always the last entry; never compressed.
- Token estimate uses 3.5 chars/token (English-prose midpoint between GPT BPE 3.8 and structured 3.3).

Client (`graph/graph.js`):

- Maintains a `chatHistory` array. On send: pushes the user msg with the turn's source list, pushes assistant msg on completion.
- Sends `{query, history, gemini_only, qwen_only}` on every turn.
- Reads `{type: "sources"}` SSE event to capture the per-turn source list for the next request's history payload.

### D3 — Adaptive RAG via tool-call (deferred to a follow-up)

Researched but **not implemented in this ADR**. The recommendation is **hybrid baseline + tool-call extension**, NOT pure agentic:

- Keep the current always-retrieve baseline (N_FACTS=10, N_CHUNKS=5) as the floor. Production research consistently shows that pure model-controlled retrieval misroutes ~34% of confidence-high cases — the baseline is the safety net.
- Expose a Gemini function `deep_search(query, top_k, scope, reason)` with `top_k <= 12`, `scope ∈ {chunks, facts, both}`, and a mandatory `reason` field that forces a justification and prevents thrash.
- Hard cap at 2 tool calls per turn, enforced at the application layer.

This is scoped out of the current change because it needs additional plumbing (Gemini function-calling integration, retrieval-loop counter, stop-criterion via cosine overlap of new vs already-injected chunks).

### D4 — UI: unified top bar + grid-native overlays

Two infrastructural UI cleanups landed alongside the RAG work because they affect how the model is invoked from the UI:

- **#topbar** — replaces the cluster of floating buttons (`#view-toggle`, `#right-toggle`, `#dev-toggle`, `#center-btn`, `#chat-header`). Single fixed bar across all views; per-view child visibility via `body[data-view=...]` selectors. Eliminates the collision issues where buttons stacked vertically when the right sidebar was open.
- **Grid-native query overlays** — overlays are now real grid children with `gridColumn`/`gridRow` set to span the primary-query group's extent. No more `getBoundingClientRect` + ResizeObserver lag. Each source belongs to exactly one group (primary_query_idx). Color palette shifted to warm hues (red/orange/amber/sand/rose) matching the app accent.

## Consequences

**Positive:**

- False refusals should drop substantially. The four-tier confidence ladder gives the model permission to answer with hedging instead of refusing.
- Follow-up questions get conversational context. Source thrash eliminated via per-turn dedup.
- 32k context window managed predictably under load. User intent signal preserved (user messages verbatim) until the very last eviction tier.
- UI button layout no longer breaks when right sidebar opens.

**Negative / risks:**

- System prompt cost: +750 tokens per request (~$0.00045 / turn on Gemini Flash). Worth it; this is rounding error vs the context block.
- Token estimate is heuristic (3.5 chars/tok). If a turn truly hits the 32k boundary, eviction may over- or under-trigger by ~10%. Acceptable for now — tighten with a real tokenizer if it bites.
- The compaction is naive: oldest assistant responses get truncated, not summarized. A real LLM-summarization pass would be higher quality but adds latency. Defer.
- No retrieval rewriting yet. Follow-ups with unresolved coreferences ("what about him?") will retrieve weakly. Agent B recommended a fast-model query rewrite step; deferred to a follow-up.

**Follow-ups (open):**

1. Implement D3 (deep_search tool-call) once the baseline change is validated.
2. LLM-based assistant-response summarization at Tier 1 instead of truncation.
3. Context-aware query rewriting before retrieval (Agent B's recommendation).
4. Real tokenizer (tiktoken or Gemini's count_tokens) instead of the 3.5-char heuristic.

## Research sources

System prompt research — Agent A:

- Anthropic prompting best practices (platform.claude.com/docs)
- Tensorlake citation-aware RAG guide
- arxiv:2510.10452 (SafeRAG steering)
- Cohere RAG documentation
- GrowwStacks RAG failure analysis

History management research — Agent B:

- NVIDIA Multi-Turn RAG Blueprint
- Anthropic Compaction Docs + Effective Context Engineering
- LangChain ConversationalRetrievalChain
- agentmarketcap.ai 2026 tiered-memory study
- MTRAG-UN benchmark

Adaptive RAG research — Agent C:

- arxiv:2506.10408 Reasoning Agentic RAG Survey
- arxiv:2510.14337 Stop-RAG (value-based retrieval control)
- arxiv:2605.00737 "To Call or Not to Call"
- Self-RAG (ICLR 2024)
- Anthropic Contextual Retrieval Cookbook
- Gemini Function Calling Guide
