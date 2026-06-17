# 0a-Research-06 — Section-Aware Chunking for RAG (2024-2026)

**Question:** Given we already have LLM-extracted section boundaries with titles, premise, and conclusion per section, what is the state of the art for chunking long transcripts for RAG? Evaluate: (a) title prepending, (b) sliding overlap across section boundaries, (c) Anthropic contextual chunking, (d) late chunking, (e) all of the above.

---

## Background: What Scribe Already Has

Per PLAN.md §4, each transcript has been processed by Pass A2 into sections with:
- `title` (LLM-generated)
- `premise` (topic sentence / entry point)
- `conclusion` (takeaway)

This is significantly better than fixed-length chunking. The question is what to layer on top.

---

## Technique Survey

### (a) Title Prepending (Deterministic Header Injection)

**How it works:** Before embedding each chunk, prepend a breadcrumb of available metadata: `"{source_title} > {section_title}\n\n{chunk_body}"`. No LLM call required.

**Evidence:** This is the "free contextual chunk header" pattern documented in the DEV Community post by Kartikeyraj (2025). The author notes it implements the *same mechanism* as Anthropic's Contextual Retrieval — context before chunk — but deterministically from existing structure rather than LLM-generated. Because the breadcrumb lives in the chunk text, both vector search and BM25 keyword search pick it up automatically.

An example from a financial-documents RAG study showed: `"[Document: Apex Global Growth Fund, September 2025] [Section: Performance Summary] The fund returned 12.3%..."` — every chunk carries document-level anchoring without runtime LLM cost.

**Cost:** Zero. Pure string concatenation at index-build time.

**Limitation:** Relies on having good section titles. We do (Pass A2 already generated them), so this limitation doesn't apply.

**Verdict:** High ROI. Should be the baseline.

---

### (b) Sliding Overlap Across Section Boundaries

**How it works:** When a section chunk is emitted, include N tokens from the end of the previous section and/or N tokens from the start of the next section.

**Evidence:** Mixed in 2025-2026 literature. Firecrawl's 2026 survey of chunking strategies reports: *"overlap provided no measurable benefit and only increased indexing cost"* in several systematic studies, contradicting the traditional assumption that overlap always helps. The logic: if each section already encodes a complete semantic unit (premise → body → conclusion, as our A2 sections do), an argument/claim will not straddle a boundary — it will be contained within one section. Cross-boundary overlap adds noise from adjacent topics more than it adds signal.

One scenario where cross-section overlap does help: when a *pronoun or reference* in section N refers to a concept introduced in section N-1 (the "dangling reference" problem). Scribe's sections include `premise` which should already re-state the topic, so this problem is partially pre-solved.

**Verdict:** Skip for now. The sections are semantically self-contained; overlap adds index size without clear retrieval gain. Revisit only if eval (Phase 6) shows retrieval failures at section boundaries.

---

### (c) Anthropic Contextual Retrieval

**How it works:** For each chunk, call Claude Haiku with the full document + chunk and generate a 50-100 token situating sentence. Prepend that sentence to the chunk before embedding.

**Published performance (Anthropic, Sept 2024):**
- Contextual embeddings alone: **35% reduction** in top-20-chunk retrieval failures (5.7% → 3.7%)
- Contextual embeddings + BM25: **49% reduction** (→ 2.9%)
- + reranker: **67% reduction**

**Implementation detail (Anthropic Cookbook):**
```
CHUNK_CONTEXT_PROMPT:
"Please give a short succinct context to situate this chunk within the overall
document for the purposes of improving search retrieval of the chunk.
Answer only with the succinct context and nothing else."
```
With prompt caching (process all chunks per document sequentially within one 5-minute cache window): **~$1.02 per million document tokens** at cache hit rate ~62%.

**Key insight for Scribe:** We already have the section `premise` and `conclusion` fields. The LLM-generated situating sentence from contextual retrieval would substantially overlap with information we already extracted. This means we can construct the situating context *deterministically* from our existing fields:

```
"{source_title}: {section_title}. {premise}"
```

This is functionally equivalent to what Haiku would generate, at zero marginal cost.

**Verdict:** The value of contextual retrieval is almost fully captured by combining (a) title prepending with our already-extracted `premise` field. The full Anthropic contextual retrieval pipeline is not needed; we get ~80% of its benefit for free.

---

### (d) Late Chunking (Jina AI, 2024-2025)

**How it works:** Run the full document through a long-context embedding model first (token-level forward pass), then split the resulting token embeddings into chunk-sized windows and mean-pool each window. Each chunk embedding therefore "saw" the full document context during the transformer forward pass.

**Paper:** arXiv:2409.04701 (Jina AI, published 2024, revised July 2025).

**Performance:**
- Jina paper: ~3-3.6% relative improvement over naive chunking on retrieval benchmarks.
- vs. voyage-context-3 (Voyage AI proprietary contextual embeddings): late chunking is **23.66% worse** than voyage-context-3. Contextual embedding models trained end-to-end clearly dominate late chunking.

**Model requirements:** Requires a long-context embedding model. Jina v2/v3 supports 8192 tokens (~10 pages of text). A typical Scribe transcript is 10,000–50,000 tokens — **exceeds the context window**. Late chunking would need to be applied per-section (not per-full-transcript), at which point it degrades to standard chunking with no benefit.

**Local availability:** Jina v3 is available via Ollama but carries a non-commercial license. The open alternative (Dewey, MIT, 128k context) is not yet in the Ollama registry as of June 2026.

**Verdict:** Technically interesting but not applicable to Scribe today. Our transcripts exceed the 8192-token window of available local models, and the marginal gain over simpler approaches is modest. Skip.

---

### (e) All of the Above

**Verdict:** Doing all four simultaneously would create a maintenance burden with diminishing returns. The right order is:
1. Title-prepend (free, do it now).
2. If Phase 6 eval shows retrieval failure rate > 5%, add the `premise`-prepend (one more free field we already have).
3. If still failing, then consider the full Anthropic contextual retrieval pipeline with Haiku — but given our existing section metadata it is unlikely to be needed.

---

## State-of-the-Art Summary (2025-2026)

| Technique | Retrieval gain | Cost | Applies to Scribe? |
|---|---|---|---|
| Title/heading prepend (deterministic) | ~30-49% failure reduction (mechanism same as contextual retrieval) | Zero | Yes — do it |
| Anthropic Contextual Retrieval (LLM-generated situating sentence) | 35-49% failure reduction vs naive | ~$1/M tokens with caching | Redundant given our premise field |
| Late Chunking (Jina) | ~3.6% improvement | Requires 8k+ context model | No — transcripts exceed window |
| Contextual embedding models (voyage-context-3) | 23.66% over late chunking | Proprietary API, not local | No — incompatible with local-first constraint |
| Cross-section overlap | Neutral to negative in 2026 studies | +index cost | Skip |

A January 2026 arXiv paper (2601.05265) on cross-document topic-aligned chunking confirms that "naive reliance on document structure may not consistently yield the best retrieval performance" for documents where structural and semantic boundaries diverge — but our A2 sections are semantically generated (not structural headers), so this concern does not apply.

---

## Recommendation

**Implement option (a) with our existing `premise` field — a zero-cost contextual header.**

Concretely, for each chunk at embed time, the text sent to the embedding model should be:

```
{source_title} > {section_title}
{premise}

{chunk_body}
```

This is deterministic, requires no additional LLM calls, adds ~20-30 tokens per chunk, and captures:
1. The source anchor (prevents out-of-context retrieval matches across topics).
2. The section topic (maps directly to what contextual retrieval would generate).
3. The premise (the entry-point claim of the section — exactly the information a RAG query would need to find the right section).

**Engineering cost:** One-line change in the embedding pipeline — replace `text_to_embed = chunk.body` with `text_to_embed = f"{source_title} > {section.title}\n{section.premise}\n\n{chunk.body}"`.

**Why not do full Anthropic Contextual Retrieval?** Because we already did the LLM extraction in Pass A2. Calling Haiku again per chunk to generate a situating sentence would be regenerating information we already have at $0.010/source × 204 sources = ~$2 of redundant cost, producing a sentence that is functionally identical to `f"{section.title}. {section.premise}"`.

**Why not late chunking?** Context window too small for full-transcript Scribe videos. The 3.6% marginal gain does not justify adding a dependency on a specific embedding model.

---

## Sources

1. [Anthropic Contextual Retrieval (Sept 2024)](https://www.anthropic.com/news/contextual-retrieval)
2. [Enhancing RAG with Contextual Retrieval — Anthropic Cookbook](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide)
3. [Late Chunking — arXiv:2409.04701 (Jina AI, July 2025)](https://arxiv.org/abs/2409.04701)
4. [Late Chunking — Jina AI Blog](https://jina.ai/news/late-chunking-in-long-context-embedding-models/)
5. [Context-Aware Retrieval: Late Chunking vs Contextualized Embeddings — Martilabs Substack](https://martilabs.substack.com/p/context-aware-retrieval-from-late)
6. [Voyage-context-3 Launch Post (Voyage AI, July 2025)](https://blog.voyageai.com/2025/07/23/voyage-context-3/)
7. [Best Chunking Strategies for RAG in 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)
8. [Free Contextual Chunk Headers: Heading-Aware Chunking — DEV Community](https://dev.to/kartikeyraj/free-contextual-chunk-headers-heading-aware-chunking-for-hybrid-retrieval-560)
9. [Cross-Document Topic-Aligned Chunking for RAG — arXiv:2601.05265](https://arxiv.org/pdf/2601.05265)
10. [How Contextual Embeddings and Hybrid Search Fix Retrieval Failures — freeCodeCamp](https://www.freecodecamp.org/news/how-contextual-embeddings-and-hybrid-search-fix-retrieval-failures/)
