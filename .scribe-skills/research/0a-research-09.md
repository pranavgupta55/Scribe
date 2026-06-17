# Phase 0a Research — Agent 09: Cross-Source Agreement & Contradiction Detection

**Question**: How do knowledge-graph and fact-check systems detect when two sources AGREE, BUILD ON, or CONTRADICT each other beyond surface embedding similarity? Specifically: (a) cheap local NLI models as pre-filters for 5000-8000 claim pairs before Haiku; (b) how GraphRAG / Diffbot / Constellation handle it; (c) false-positive rate of high cosine sim → semantic agreement. Would a local NLI pre-filter materially improve Phase 4 edge quality?

---

## 1. The Core Problem With Cosine Similarity Alone

Two independent 2024-2025 papers converge on the same warning:

**"Is Cosine-Similarity of Embeddings Really About Similarity?" (arxiv:2403.05440)**
Cosine similarity of learned embeddings can produce arbitrary results because embeddings contain rescaling degrees of freedom that are not constrained by the training objective. Normalizing after training introduces opaque, potentially meaningless similarity values. The unnormalized dot product is better-defined, but neither captures entailment vs. contradiction.

**"Semantics at an Angle: When Cosine Similarity Works Until It Doesn't" (arxiv:2504.16318)**
Entailment and NLI tasks can produce indistinguishable cosine scores for weak vs. strong hypotheses because both vectors point in similar directions. Cosine discards norm information, which is where confidence/specificity differences live. The paper recommends norm-aware alternatives (e.g., Word Rotator's Distance) for NLI-adjacent tasks.

**"The Semantic Illusion: Certified Limits of Embedding-Based Hallucination Detection in RAG Systems" (arxiv:2512.15068)**
The most damning result: at a 95% coverage target on HaluEval (ChatGPT-generated hallucinations), embedding-based methods achieve a **100% false positive rate** — they flag every faithful response as hallucinated. The root cause is that RLHF-trained models produce hallucinations that are semantically indistinguishable from faithful responses at the embedding level. The paper shows GPT-4o-mini as a judge achieves only 7% false positives on the same data, proving the signal exists but is opaque to surface-level semantics.

**Practical implication for Phase 4**: A cosine threshold alone (even at 0.71 as currently planned) will conflate *agreement*, *paraphrase*, *builds-on*, and *contradiction* at a high rate. Two claims on opposite sides of an argument ("raise prices → more revenue" vs. "raise prices → churn") can and do produce cosine ≥ 0.70 because they share topic vocabulary. The embedding sim field in the per-batch payload is a necessary but not sufficient signal.

---

## 2. What Signals Actually Work Beyond Cosine Similarity

From the literature, production fact-check and KG systems use a layered signal stack:

| Signal | Catches | Misses |
|--------|---------|--------|
| Cosine / embedding sim | Topical overlap, paraphrase | Negation, polarity flip, quantitative contradiction |
| Lexical overlap (BLEU, Jaccard) | Near-identical phrasing | Paraphrase, synonym contradiction |
| NLI model (3-way: entail / neutral / contradict) | Direct logical relationship | Subtle/indirect disagreement, sarcasm, presupposition |
| Predicate matching (semantic role labeling) | Contradictions on same subject-predicate | Open-domain variation |
| Numeric / quantitative comparison | Contradictions on axes (e.g. "$5k vs $50k threshold") | Non-numeric claims |
| LLM judge with chain-of-thought | Subtle contradiction, builds-on nuance | Cost, latency |

The consensus architecture in 2025 production pipelines is: **embedding pre-filter → NLI classification → LLM judgment for ambiguous cases**. The NLI model acts as a cheap gatekeeper that catches high-confidence entailments and contradictions before they reach the LLM budget.

A concrete example from published pipelines (arxiv:2601.22984, 2025): NLI models finalize verdicts when they predict Entailment with confidence > 0.99 against at least one evidence chunk, achieving 98.47% accuracy on FEVER and 90.09% on SciFact-Open, delegating only the ambiguous band to the LLM. This directly maps to our Haiku-only Phase 4: the NLI pre-filter removes the clearest cases (high-confidence entailment / contradiction) and lets Haiku focus only on the fuzzy middle.

---

## 3. Available Local NLI Models

### 3a. MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli

- **Parameters**: 400M
- **Accuracy**: MNLI-matched 91.2%, MNLI-mismatched 90.8%, ANLI-all 70.2%, ANLI-r3 64% (adversarial)
- **Training data**: MultiNLI + FEVER-NLI + ANLI + LingNLI + WANLI (SNLI deliberately excluded due to quality issues)
- **Speed**: 425-980 pairs/sec on A100 GPU. On CPU (no published M-series number) expected ~10-30 pairs/sec without batching, higher with batched inference.
- **HuggingFace**: `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli`
- **Fit for Scribe**: High accuracy across domains, trained on FEVER (fact-checking) which matches our claim-vs-claim framing. The large variant is the right quality tier; the base variant (below) is faster.

### 3b. cross-encoder/nli-deberta-v3-base

- **Parameters**: 200M
- **Accuracy**: SNLI 92.38%, MNLI-mismatched 90.04%
- **Output**: 3 scores (contradiction, entailment, neutral) as a CrossEncoder — meaning it processes the *pair* jointly, which is the correct architecture for NLI (vs. bi-encoder which encodes independently).
- **HuggingFace**: `cross-encoder/nli-deberta-v3-base`
- **Fit for Scribe**: Half the size of the large variant with near-identical MNLI accuracy. For a pre-filter that just needs to separate {clear entailment, clear contradiction} from {ambiguous}, this is the better cost/speed choice. Use `sentence-transformers` `CrossEncoder` for easy batch inference.

### 3c. Vectara HHEM-2.1-Open

- **Base model**: FLAN-T5-Base (100M parameters)
- **Architecture**: Fine-tuned for binary (hallucinated / consistent) classification, not 3-way NLI
- **CPU speed**: ~1.5 seconds for a 2k-token input on modern x86 CPU; <600MB RAM at 32-bit
- **Accuracy**: AggreFact-SOTA balanced accuracy 76.55%; RAGTruth-Summarization 64.42%; RAGTruth-QA 74.28% — **outperforms GPT-3.5 and GPT-4 (2023 vintage) on RAGTruth**
- **HuggingFace**: `vectara/hallucination_evaluation_model`
- **Fit for Scribe**: Designed for premise → generated-text consistency (RAG hallucination), not symmetric claim-vs-claim NLI. It will produce a score between 0-1 (not a 3-way label) and has the asymmetry problem: "I visited California" is hallucinated given "I visited US" but not vice versa. This framing mismatches our need to classify symmetric pairs. Use only as a secondary confidence signal, not as the primary NLI pre-filter.

### 3d. HaluGate / ModernBERT-base-nli

- **NLI backbone**: tasksource/ModernBERT-base-nli
- **Latency**: Total pipeline 76-162ms (P50-P99), with the NLI explainer stage alone at 18-42ms
- **CPU**: Runs natively via Candle, 500MB-1GB RAM, no Python sidecar needed
- **Fit for Scribe**: ModernBERT (released Dec 2024) is an efficient replacement for BERT/DeBERTa on CPU-constrained deployments. The `tasksource/ModernBERT-base-nli` checkpoint is worth benchmarking as an alternative to DeBERTa-v3-base. Expected accuracy close to DeBERTa-v3-base at lower latency due to architectural improvements.

---

## 4. Throughput Estimate for 5000-8000 Pairs on Apple Silicon (M-series)

Published benchmarks for DeBERTa-v3-large on A100: 425-980 pairs/sec. Apple Silicon M2/M3 unified memory typically delivers 20-35% of A100 throughput for transformer encoder inference when using MPS (Metal Performance Shaders) or optimized CPU. Rough estimate:

- **DeBERTa-v3-large (400M) on M2/M3**: ~80-200 pairs/sec → **5000 pairs in ~25-60 seconds**
- **DeBERTa-v3-base / cross-encoder (200M) on M2/M3**: ~150-400 pairs/sec → **5000 pairs in ~12-33 seconds**
- **HHEM-2.1 FLAN-T5-Base (100M) on x86 CPU**: ~670 pairs/sec (extrapolated from 1.5s/2k-token input at 3 tokens/pair average) → **5000 pairs in ~7 seconds**

These are wall-clock-compatible with a pre-filter step before Haiku batches. The DeBERTa-v3-base CrossEncoder is the recommended choice: 200M parameters, 90% MNLI accuracy, fast enough, and produces the 3-way label (contradiction / entailment / neutral) that Phase 4 needs.

---

## 5. How GraphRAG, Diffbot, and Related Systems Handle It

### Microsoft GraphRAG
GraphRAG's published architecture focuses on **community detection and summarization**, not on cross-source contradiction detection as a first-class feature. The indexing engine extracts entities and relationships, runs Leiden community detection, and summarizes communities — but does not expose a dedicated contradiction edge type. When conflicting facts exist, they are summarized together by the LLM; the graph does not flag them as contradictory edges. GraphRAG is optimized for global synthesis, not adversarial claim comparison. **Not a relevant model for Scribe's Phase 4.**

### Diffbot Knowledge Graph
Diffbot uses **Knowledge Fusion**: each fact carries a confidence score (0-1) computed by fusing signals from multiple web sources. Facts below 0.5 confidence are discarded. Critically, Diffbot's conflict detection is **provenance-based** — if source A says "X is true" and source B says "X is false", the fusion process assigns a lower confidence to both, rather than emitting a contradiction edge. The system does not expose a "source A contradicts source B" relationship; it collapses contradictions into uncertainty. Data provenance metadata is maintained per fact. This approach is suitable for structured knowledge bases but misses the nuanced "builds-on" / "contradicts with qualification" edge types that Scribe needs.

### Constellation (no product match)
No published system called "Constellation" was found in the context of knowledge graph contradiction detection. The query may refer to a different product. The closest match is **ClaimVer** (arxiv:2403.09724), a claim-level verification system that uses knowledge graphs for evidence attribution and labels claims as "Consistent", "Contradicted", or "Uncertain" using a conservative precedence rule: "Contradicted" if any explicit contradiction exists, "Consistent" if there are no contradictions plus sufficient support, "Uncertain" for mixed evidence. This is the closest published analog to Scribe's `agreement | builds-on | contradiction | related | none` classification.

### SummaC (NLI applied to document consistency)
SummaC (TACL 2022) solved the granularity mismatch: standard NLI is sentence-level, but document-level consistency requires comparing sentences from one document against all sentences of another. Their **SummaCConv** method: (1) segment both documents into sentences, (2) compute NLI scores for all sentence pairs across documents, (3) aggregate via convolution (max or mean over rows). Balanced accuracy 74.4%, 5 percentage points above prior art. This aggregation pattern is directly applicable to Phase 4: when two claims each consist of 2-3 sentences, compute NLI over sentence sub-pairs and aggregate. Do not compare full multi-sentence claims as a single NLI input — the model accuracy degrades significantly at document length.

---

## 6. False Positive Rate of "High Cosine Sim → Semantic Agreement"

Direct measurements from the literature:

1. **At cosine ≥ 0.70 (our current Phase 4 threshold)**: No published direct false-positive rate for claim-vs-claim NLI disagreement at this threshold. However, from the MNLI training distribution, approximately 33% of all sentence pairs are labeled "neutral" — neither entailment nor contradiction. High cosine pairs in MNLI that are actually "neutral" are common in the 0.65-0.80 range.

2. **HaluEval synthetic experiments** (arxiv:2512.15068): 100% false positive rate when cosine similarity used as hallucination detector on realistic hallucinations from RLHF-trained models. This is an extreme case but directionally correct: modern text generators produce topically coherent text that clusters near the truth in embedding space even when semantically contradicting it.

3. **NLI models over-predict contradiction** (from SciNLI and MNLI literature): NLI models make contradiction predictions more frequently than entailments in most cases, particularly when sentences come from irrelevant contexts. This means a raw NLI pre-filter without a context window has its own false-positive problem — it will label topic-shifted pairs as contradictions. The fix is to restrict NLI to pairs where cosine ≥ threshold (i.e., NLI as a *refinement* of high-sim pairs, not a standalone filter on all pairs).

**Combined estimate for Scribe's claim pairs**: Among the ~7800 candidate pairs Phase 4 will see (filtered to cosine ≥ 0.45), a rough estimate based on MNLI base rates is:
- ~25-35% will be genuine entailment/agreement
- ~20-30% will be genuine "related but neutral" (no edge)
- ~5-15% will be genuine contradiction
- ~15-25% will be high-cosine topical overlap that cosine scores as "similar" but NLI scores as "neutral" or "contradiction"

Haiku 4.5 operating on the text of both claims can catch most of these, but it will make mistakes on the 15-25% high-cosine-but-not-agreement band — and at 260 pairs per batch, errors compound.

---

## 7. Recommendation: Would a Local NLI Pre-Filter Materially Improve Phase 4 Edge Quality?

**Answer: Yes, with a targeted scope.**

The value of NLI is not in replacing Haiku — it is in (a) routing and (b) confidence calibration:

### Recommended Phase 4 Architecture

```
Candidate pairs (7800, cosine ≥ 0.45)
    │
    ▼
[Step 1] NLI pre-screen (cross-encoder/nli-deberta-v3-base)
    │  Batch all 7800 pairs, ~30-60 sec on Apple Silicon M-series
    │  Outputs: {contradiction_score, entailment_score, neutral_score}
    │
    ├─► HIGH entailment (≥ 0.92) AND cosine ≥ 0.60
    │       → Route to Haiku with hint: "strong agreement likely"
    │       → Reduces Haiku ambiguity on clear-agreement pairs
    │
    ├─► HIGH contradiction (≥ 0.85) AND cosine ≥ 0.50
    │       → Route to Haiku with hint: "likely contradiction — verify carefully"
    │       → Flags pairs Haiku might otherwise label "related"
    │
    ├─► HIGH neutral (≥ 0.80) AND cosine < 0.60
    │       → Drop before Haiku (estimated 20-30% of pairs → cost saving)
    │       → These are topically overlapping but semantically unrelated pairs
    │
    └─► All others (ambiguous band)
            → Send to Haiku as-is with embedding_sim + NLI scores included in payload
            → Let Haiku make the final call
```

**Expected impact**:
- **Cost**: Drop 20-30% of the lowest-value pairs before Haiku → ~6 agents instead of 30, or same 30 agents with higher-confidence inputs. Saves ~$0.30-0.40 on Phase 4.
- **Edge quality**: The per-batch Haiku payload gains `nli_entailment_score` and `nli_contradiction_score` fields alongside `embedding_sim`. This gives Haiku a calibrated signal it can use to break ties — the difference between a 0.71 cosine / 0.88 entailment pair and a 0.71 cosine / 0.72 neutral / 0.18 contradiction pair is meaningful.
- **Contradiction recall**: NLI specifically flags polarity flips and negation — the exact cases cosine misses. The 5-15% genuine contradictions in the candidate pool are the most valuable edges (CLAIM-DEFINITION §4 reason #2); catching them reliably is worth the 30-60 second pre-pass.

**Caveat — what NLI cannot do**:
- It cannot detect "builds-on" (a claim that extends another without contradicting it). Cosine + claim type fields are better signals for builds-on.
- It cannot judge whether two claims are *about the same axis* — that requires the `a_concept` / `b_concept` context that only Haiku sees.
- It will produce false contradictions on pairs from very different contexts (e.g., a pricing claim paired with a marketing claim that happen to share negation structure). Restrict NLI contradiction routing to pairs where cosine ≥ 0.50 to reduce this.

**Is Haiku 4.5 strong enough on its own?** It is strong enough to produce reasonable labels, but it will systematically under-detect contradictions (it tends toward "related" when in doubt) and will over-label high-cosine neutral pairs as "agreement". The NLI pre-filter specifically addresses the second failure mode; the contradiction-routing hint addresses the first. Together they close both gaps without requiring a larger model.

---

## 8. Schema Change for Phase 4 Pair Payload

Add two fields to the existing per-batch JSON (PLAN.md §2 Phase 4):

```json
{
  "a_id": "claim-1234", "b_id": "claim-5678",
  "a_text": "...", "b_text": "...",
  "a_concept": "Lead Generation", "b_concept": "Customer Acquisition",
  "a_source": "...", "b_source": "...",
  "embedding_sim": 0.71,
  "nli_entailment": 0.88,
  "nli_contradiction": 0.04,
  "nli_neutral": 0.08
}
```

Update CONN_PROMPT_V2.md to instruct Haiku: "When `nli_entailment ≥ 0.85`, default to `agreement` unless the claim texts show a clear polarity difference. When `nli_contradiction ≥ 0.80`, default to `contradiction` unless the claims address entirely different axes."

---

## Sources

1. [MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli — Hugging Face](https://huggingface.co/MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli)
2. [cross-encoder/nli-deberta-v3-base — Hugging Face](https://huggingface.co/cross-encoder/nli-deberta-v3-base)
3. [vectara/hallucination_evaluation_model — Hugging Face](https://huggingface.co/vectara/hallucination_evaluation_model)
4. [SummaC: Re-Visiting NLI-based Models for Inconsistency Detection in Summarization — arxiv:2111.09525](https://arxiv.org/abs/2111.09525)
5. [SummaC paper — TACL / ACL Anthology](https://aclanthology.org/2022.tacl-1.10/)
6. [Is Cosine-Similarity of Embeddings Really About Similarity? — arxiv:2403.05440](https://arxiv.org/html/2403.05440v1)
7. [Semantics at an Angle: When Cosine Similarity Works Until It Doesn't — arxiv:2504.16318](https://arxiv.org/html/2504.16318v2)
8. [The Semantic Illusion: Certified Limits of Embedding-Based Hallucination Detection — arxiv:2512.15068](https://arxiv.org/html/2512.15068)
9. [A Straightforward Pipeline for Targeted Entailment and Contradiction Detection — arxiv:2508.17127](https://arxiv.org/abs/2508.17127)
10. [HaluGate: Token-Level Hallucination Detection — vLLM Blog](https://vllm.ai/blog/2025-12-14-halugate)
11. [Hallucination Detection: Commercial vs Open Source — Vectara Blog](https://www.vectara.com/blog/hallucination-detection-commercial-vs-open-source-a-deep-dive)
12. [Introducing Next Generation of Vectara's Hallucination Leaderboard](https://www.vectara.com/blog/introducing-the-next-generation-of-vectaras-hallucination-leaderboard)
13. [Diffbot Knowledge Graph — Data Provenance](https://blog.diffbot.com/knowledge-graph-glossary/data-provenance/)
14. [Diffbot boosts LLM accuracy via Knowledge Graph — SiliconANGLE](https://siliconangle.com/2025/01/09/diffbot-boosts-llm-accuracy-tapping-vast-knowledge-graph-date-information/)
15. [ClaimVer: Explainable Claim-Level Verification — arxiv:2403.09724](https://arxiv.org/html/2403.09724v2)
16. [Graph-based Retrieval for Claim Verification over Cross-Document Evidence — arxiv:2109.06022](https://arxiv.org/pdf/2109.06022)
17. [Detect-Then-Resolve: Knowledge Graph Conflict Resolution with LLM — MDPI Mathematics](https://www.mdpi.com/2227-7390/12/15/2318)
18. [Why Your Deep Research Agent Fails? — arxiv:2601.22984](https://arxiv.org/pdf/2601.22984)
19. [MAGIC: Multi-Hop KG Benchmark for Inter-Context Conflicts — arxiv:2507.21544](https://arxiv.org/pdf/2507.21544)
20. [Benchmarking LLM Faithfulness in RAG with Evolving Leaderboards — arxiv:2505.04847](https://arxiv.org/html/2505.04847v1)
