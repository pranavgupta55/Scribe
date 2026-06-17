# Phase 0a Research — Agent 04: Local Embedding Models for Short-Text Clustering & Retrieval

**Question:** What is the current (late-2025 / 2026) best LOCAL embedding model for short-text (2–5 word topic labels) clustering AND retrieval, available via ollama?

---

## 1. Candidate Model Profiles

### 1a. nomic-embed-text v1.5
- **MTEB Overall (English v1):** 62.28
- **Dimensions:** 768 (Matryoshka — reduces gracefully to 512 / 256 / 128 / 64)
- **Context window:** 8,192 tokens
- **Ollama size:** 274 MB
- **Ollama tag:** `ollama pull nomic-embed-text`
- **Task prefixes required:** yes — `clustering:`, `search_document:`, `search_query:`
- **Throughput (M2 Max, batch 128):** ~9,340 tokens/sec
- **MTEB clustering score:** ~42.56 (from search result cross-reference)
- **Notes:** Fastest and smallest of the candidates. Matryoshka support means you can halve index size with only ~1.2 pts MTEB loss (768→512: 61.96; 768→256: 61.04). Strong for speed-critical pipelines. Clustering score is the lowest of the five, which matters for Phase 1a HDBSCAN over 1,472 short labels.

**Sources:** [nomic-embed-text-v1.5 HF model card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), [morphllm benchmark](https://www.morphllm.com/ollama-embedding-models)

---

### 1b. mxbai-embed-large v1
- **MTEB Overall (English v1):** 64.68 (SOTA for BERT-large class at release, March 2024)
- **MTEB Clustering:** 46.71
- **MTEB Retrieval:** 54.39
- **MTEB STS:** 85.00
- **Dimensions:** 1,024 (MRL / Matryoshka — 512d also supported)
- **Context window:** 512 tokens
- **Model size:** 335 M params (0.3B), 670 MB
- **Ollama tag:** `ollama pull mxbai-embed-large` (also `mxbai-embed-large:335m`)
- **Throughput (M2 Max, batch 64):** ~6,780 tokens/sec
- **Notes:** Highest overall MTEB of the five BERT-class candidates. Also highest clustering score in this set at 46.71. Trained on 700M+ pairs + 30M high-quality triplets using AnglE loss; explicitly no MTEB overlap, suggesting good generalization. **Critical limitation: 512-token context window** — fine for 2–5 word topic labels, but will truncate long claim texts at the 512-token boundary. Retrieval queries need the prefix `Represent this sentence for searching relevant passages:`.

**Sources:** [mxbai-embed-large HF card](https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1), [mixedbread blog](https://www.mixedbread.com/blog/mxbai-embed-large-v1), [ollama library](https://ollama.com/library/mxbai-embed-large)

---

### 1c. bge-large-en-v1.5
- **MTEB Overall:** 64.23
- **MTEB Clustering:** 46.08
- **MTEB Retrieval:** 54.29
- **Dimensions:** 1,024
- **Context window:** 512 tokens
- **Model size:** ~300 M params, ~1.2 GB
- **Ollama tag:** `ollama pull bge-large-en`  *(note: the HF model is BAAI/bge-large-en-v1.5; ollama library uses `bge-large` or the full name — verify with `ollama search bge` before pull)*
- **Notes:** Very close to mxbai-embed-large on all metrics — 0.45 pts lower overall, 0.63 pts lower clustering, 0.1 pts lower retrieval. **Known issue:** similarity scores cluster tightly in [0.6, 1.0] range due to contrastive training at temperature 0.01 — HDBSCAN and cosine-distance thresholds must be calibrated accordingly (use 0.8–0.9 threshold). English-only.

**Sources:** [bge-large-en-v1.5 HF card](https://huggingface.co/BAAI/bge-large-en-v1.5)

---

### 1d. snowflake-arctic-embed-l
- **MTEB Retrieval (NDCG@10):** 55.98 — best retrieval of the five BERT-class models
- **MTEB Overall:** not published as a single number; retrieval-focused model
- **MTEB Clustering:** not published directly; `snowflake-arctic-embed-m-v2.0` scores 43.6 on clustering
- **Dimensions:** 1,024
- **Context window:** 512 tokens
- **Model size:** 335 M params, 669 MB
- **Ollama tag:** `ollama pull snowflake-arctic-embed:l`
- **Notes:** Purpose-built for retrieval; ranks above mxbai, bge-large, and nomic on MTEB retrieval. Lack of published clustering score is a red flag for Phase 1a clustering use case. Retrieval advantage over mxbai (+1.59 NDCG pts) makes it attractive for Phase 4 connection retrieval but weaker justification for Phase 1a clustering.

**Sources:** [snowflake-arctic-embed-l HF card](https://huggingface.co/Snowflake/snowflake-arctic-embed-l), [ollama tags](https://ollama.com/library/snowflake-arctic-embed/tags)

---

### 1e. bge-m3
- **MTEB Overall:** ~63.0 (dense mode only; hybrid mode scores differ by track)
- **MTEB Clustering:** not published as a single score; hybrid dense+sparse+multi-vector retrieval complicates direct comparison
- **MTEB Retrieval (cross-lingual, needle-in-haystack):** dominant — 0.940 R@1 cross-lingual vs 0.120 for mxbai and 0.154 for nomic
- **Practical retrieval accuracy (Paul Graham essays, top-10):** 72% vs mxbai 59.3% and nomic 57.3%
- **Dimensions:** 1,024 dense + sparse vectors
- **Context window:** 8,192 tokens
- **Model size:** 567 M params, 1.2 GB
- **Ollama tag:** `ollama pull bge-m3` (also `bge-m3:567m`)
- **Notes:** The multilingual, long-context powerhouse. English-only MTEB clustering data is not cleanly available, but its 568M parameters and hybrid retrieval design make it heavier to embed with (roughly 1.5–2× slower than mxbai per token). For a monolingual English corpus of short labels, bge-m3 provides no advantage over mxbai-embed-large or bge-large-en-v1.5 on clustering quality while costing more compute and RAM. Its retrieval advantage only surfaces on long documents and cross-lingual pairs.

**Sources:** [bge-m3 HF card](https://huggingface.co/BAAI/bge-m3), [ollama library](https://ollama.com/library/bge-m3), [embedding benchmark 2026](https://zc277584121.github.io/rag/2026/03/20/embedding-models-benchmark-2026.html), [TigerData RAG benchmark](https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag)

---

### 1f. Qwen3-Embedding (bonus — emerged during research)
New model series released May–June 2025, now in the ollama library. Ranks #1 on MTEB multilingual (8B: 70.58). The 0.6B variant (639 MB) shows **MTEB clustering: 52.33** and **MTEB retrieval: 80.83** — both substantially above all five original candidates. The 4B variant scores 57.15 / 85.05.

- **Ollama tags:** `qwen3-embedding:0.6b` (639 MB), `qwen3-embedding:4b` (2.5 GB), `qwen3-embedding:8b` (4.7 GB)
- **Context window:** 32K tokens
- **Notes:** Supports task instructions for clustering; ~1% to 5% MTEB gain when instructions used. Designed from a foundation LLM (not a BERT encoder), so embedding speed per token is lower than mxbai (~2–4× slower on CPU-bound M-series). However the clustering/retrieval quality gap over mxbai-embed-large is large enough to matter for Phase 1a.

**Sources:** [Qwen3-Embedding GitHub](https://github.com/QwenLM/Qwen3-Embedding), [ollama library](https://ollama.com/library/qwen3-embedding), [morphllm benchmark](https://www.morphllm.com/ollama-embedding-models)

---

## 2. Head-to-Head Comparison Table

| Model | MTEB Overall | Clustering | Retrieval | Dims | Ctx | Ollama size | Ollama tag |
|---|---|---|---|---|---|---|---|
| qwen3-embedding:0.6b | ~66+ (EN) | **52.33** | **80.83** | 1024 | 32K | 639 MB | `qwen3-embedding:0.6b` |
| mxbai-embed-large | 64.68 | **46.71** | 54.39 | 1024 | 512 | 670 MB | `mxbai-embed-large` |
| bge-large-en-v1.5 | 64.23 | 46.08 | 54.29 | 1024 | 512 | ~1.2 GB | `bge-large-en` |
| snowflake-arctic-embed:l | N/A (retrieval-only) | ~43 (est.) | **55.98** | 1024 | 512 | 669 MB | `snowflake-arctic-embed:l` |
| bge-m3 | ~63 (dense) | N/A | ≫ (multilingual) | 1024 | 8192 | 1.2 GB | `bge-m3` |
| nomic-embed-text v1.5 | 62.28 | 42.56 | 49.01 | 768 | 8192 | 274 MB | `nomic-embed-text` |

*Qwen3-Embedding MTEB overall numbers are on the multilingual leaderboard; English sub-scores are somewhat higher than the 66+ figure implies for this English-only task.*

---

## 3. Short-Text Specifics (2–5 word topic labels)

The 2–5 word regime is the hardest for embedding models because:
- Low lexical density → the model must rely on subword semantics and training data frequency
- No disambiguation context (e.g., "Lead Generation" vs "Lead Qualification" look similar syntactically)
- Cosine distances compress toward 1.0 in BERT-class models with contrastive training at low temperature (bge-large known issue)

MTEB's clustering tasks (e.g., ArXiv, Reddit, StackExchange clustering) use short-to-medium snippets, so the clustering sub-score is the best proxy available for 2–5 word performance. On this metric the ranking is:

**qwen3-embedding:0.6b (52.33) > mxbai-embed-large (46.71) ≥ bge-large-en-v1.5 (46.08) > bge-m3 (unclear) > snowflake-arctic (unclear) > nomic-embed-text (42.56)**

The Qwen3 gap (+5.6 pts clustering over mxbai) is material. For Phase 1a where we need to reliably collapse 1,472 topic strings → 200–350 canonical concepts, a cleaner embedding space meaningfully reduces Haiku correction work in Phase 1a verification.

---

## 4. Context Window for Long Claims

Phase 2 also embeds ~10,000 longer claim texts (avg ~100–250 tokens each, occasionally longer). Models with 512-token context (mxbai, bge-large, snowflake-arctic) will not truncate these claims (100–250 tokens < 512). Qwen3-embedding:0.6b's 32K window is overkill but harmless.

---

## 5. Speed Estimates — M4 Mac

M4 Mac baseline: approximately **1.3× faster than M2 Max** for neural net inference based on published Apple Silicon generational benchmarks (M4's 38 TOPS vs M2 Max's ~22 TOPS on Neural Engine; real-world embedding throughput scales roughly proportionally).

Derived estimates (applying 1.3× to M2 Max numbers where available):

| Model | M2 Max (known) | M4 estimate | 5k embeds (avg 10 tok) | 10k embeds (avg 80 tok) |
|---|---|---|---|---|
| nomic-embed-text | 9,340 tok/s | ~12,100 tok/s | ~4 sec | ~66 sec (~1 min) |
| mxbai-embed-large | 6,780 tok/s | ~8,800 tok/s | ~6 sec | ~90 sec (~1.5 min) |
| qwen3-embedding:0.6b | est. 3,000–4,500 tok/s* | ~4,000–5,800 tok/s* | ~12–18 sec | ~150–220 sec (~2.5–3.5 min) |
| bge-m3 | est. 3,000–3,500 tok/s* | ~4,000 tok/s* | ~15 sec | ~200 sec (~3.3 min) |

*Foundation-LLM-based models (Qwen3) and larger BERT models (bge-m3) lack published ollama throughput figures; these estimates extrapolate from model size and architecture class.

**For Scribe's batch (5,000 short labels + 10,000 longer claim texts, total ~850K tokens):**

- mxbai-embed-large: ~850K / 8,800 ≈ **~97 seconds (~1.5–2 min)**
- qwen3-embedding:0.6b: ~850K / 5,000 ≈ **~170 seconds (~3 min)**
- nomic-embed-text: ~850K / 12,100 ≈ **~70 seconds (~1 min)**

All three are well inside the "acceptable" range for a one-time re-embedding step. Wall-clock is dominated by RAM bandwidth and model load, not pure throughput math, so real times will be 1.5–2× longer than the arithmetic above: **mxbai ≈ 3–4 min, Qwen3-0.6b ≈ 5–7 min, nomic ≈ 2–3 min**.

---

## 6. Bottom Line

### Recommendation for Scribe

**Primary recommendation: `qwen3-embedding:0.6b`**

Rationale:
1. Highest MTEB clustering score (52.33) of any ollama-available model — +5.6 pts over the best BERT-class alternative (mxbai-embed-large). This translates directly to fewer split/merge corrections in Phase 1a Haiku verification.
2. Highest MTEB retrieval score (80.83) — ensures Phase 4 connection retrieval (finding semantically-related claims across sources) also improves substantially.
3. 32K context window handles all claim texts without truncation.
4. Download size (639 MB) is comparable to mxbai (670 MB) — essentially the same footprint on disk.
5. The ~2× speed penalty vs mxbai still produces a total embed time of ~5–7 min for the full corpus on M4 — acceptable for a one-time Phase 2 operation.

**Fallback (if qwen3-embedding:0.6b has quality issues on English short labels in practice):** `mxbai-embed-large` — best BERT-class English model, highest clustering score in its class (46.71), 670 MB, straightforward ollama operation.

Do NOT choose:
- **nomic-embed-text**: lowest clustering score (42.56); speed advantage doesn't compensate.
- **bge-large-en-v1.5**: tiny MTEB advantage vs mxbai, with the known similarity-compression bug requiring threshold calibration.
- **snowflake-arctic-embed:l**: retrieval-optimized, clustering score unclear; adds risk to Phase 1a.
- **bge-m3**: multilingual overhead wasted on English-only corpus; no cluster-score data; heaviest model.

### Specific ollama tag to pull

```
ollama pull qwen3-embedding:0.6b
```

Fallback:
```
ollama pull mxbai-embed-large
```

### Expected wall-clock on M4 Mac

| Scenario | Time estimate |
|---|---|
| 5,000 short topic labels (avg ~5 tokens) + 10,000 longer claim texts (avg ~80 tokens) | **5–8 min total** for qwen3-embedding:0.6b |
| Same corpus with mxbai-embed-large | **3–5 min total** |

These estimates assume batch embedding via ollama's `/api/embed` endpoint and include model cold-start load time (~15–30 sec first call).

---

## 7. Implementation Notes for Phase 2

1. **Prefix for clustering:** When embedding topic labels for clustering, use the instruction prefix: `"Instruct: Retrieve semantically similar text.\nQuery: "` (or the task-specific instruction the Qwen3 docs recommend). For claim texts embedded as documents, use the document variant.
2. **Dimension:** Default 1,024d; no need to truncate for a corpus of 15,000 items — index size is negligible.
3. **Batch size:** Start with batch=32–64 for qwen3-embedding:0.6b via ollama to avoid timeout; mxbai can handle batch=128.
4. **Verify before Phase 1a:** Run a quick sanity check: embed the top-20 most-duplicated topic strings (e.g., all variants of "lead generation"), compute pairwise cosine similarities, and confirm near-duplicates score ≥ 0.88 before committing to the full embed run.

---

## Sources

1. [MTEB Leaderboard — Hugging Face Spaces](https://huggingface.co/spaces/mteb/leaderboard)
2. [nomic-embed-text-v1.5 — HuggingFace model card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
3. [mxbai-embed-large-v1 — HuggingFace model card](https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1)
4. [Mixedbread blog: mxbai-embed-large-v1](https://www.mixedbread.com/blog/mxbai-embed-large-v1)
5. [bge-large-en-v1.5 — HuggingFace model card](https://huggingface.co/BAAI/bge-large-en-v1.5)
6. [bge-m3 — HuggingFace model card](https://huggingface.co/BAAI/bge-m3)
7. [snowflake-arctic-embed-l — HuggingFace model card](https://huggingface.co/Snowflake/snowflake-arctic-embed-l)
8. [Ollama library: snowflake-arctic-embed tags](https://ollama.com/library/snowflake-arctic-embed/tags)
9. [Ollama library: mxbai-embed-large](https://ollama.com/library/mxbai-embed-large)
10. [Ollama library: bge-m3](https://ollama.com/library/bge-m3)
11. [Ollama library: qwen3-embedding](https://ollama.com/library/qwen3-embedding)
12. [Qwen3-Embedding GitHub (QwenLM)](https://github.com/QwenLM/Qwen3-Embedding)
13. [Best Ollama Embedding Models 2026 — morphllm.com](https://www.morphllm.com/ollama-embedding-models)
14. [Which Embedding Model Should You Actually Use in 2026? — Cheney Zhang](https://zc277584121.github.io/rag/2026/03/20/embedding-models-benchmark-2026.html)
15. [Finding the Best Open-Source Embedding Model for RAG — TigerData](https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag)
16. [Best Embedding Models 2025: MTEB Scores — ailog.fr](https://app.ailog.fr/en/blog/guides/choosing-embedding-models)
17. [Ollama Embedded Models Guide 2025 — Collabnix](https://collabnix.com/ollama-embedded-models-the-complete-technical-guide-for-2025-enterprise-deployment/)
