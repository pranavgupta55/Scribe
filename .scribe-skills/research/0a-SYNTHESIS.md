# Phase 0 Synthesis — Decisions for the Graph Rebuild

_Written after reading: PLAN.md, NODE-QUALITY-RUBRIC.md, CLAIM-DEFINITION.md, HIERARCHY.md, FEW-SHOT.md, 0a-research-01..10.md, and the 239-node 0b-diagnostic-corpus.jsonl._

---

## §1 — Confirmed decisions (PLAN §−1, with research citation)

These six decisions were pinned in PLAN.md §−1. The synthesis confirms each and adds any nuance.

### 1.1 Embedding model = `qwen3-embedding:0.6b` (primary), `mxbai-embed-large` (fallback)

**Confirmed.** [0a-04] provides the full head-to-head table: Qwen3-0.6b scores 52.33 on MTEB clustering vs 46.71 for mxbai-embed-large, a +5.6-point gap that is meaningful for collapsing 1472 → 300 topics. The 32K context window handles even the longest claim texts without truncation. [0a-04] also confirms the ~5–7 min wall-clock estimate on M4.

**Nuance from 0a-04 §7:** Embed topic labels for clustering using the Qwen3 task instruction prefix (`"Instruct: Retrieve semantically similar text.\nQuery: "`). Without this prefix, Qwen3's instruction-following architecture may lose 1–5% MTEB points. Run a quick sanity check before Phase 1a: embed the top-20 most-duplicated topic strings and confirm near-duplicates score ≥ 0.88 cosine similarity.

### 1.2 Clustering algorithm = Agglomerative Ward on L2-normalized embeddings

**Confirmed.** [0a-05] provides the detailed algorithm comparison. The November 2025 arxiv paper it cites directly found that "KMeans and HAC guided by a spectral cluster-count estimator significantly outperform HDBSCAN, OPTICS, and Leiden on short-text data" — precisely our input type. Ward with `n_clusters=300` on L2-normalized embeddings, one full tree fit, `fcluster` cuts at multiple k values: this is the canonical choice.

**Nuance from 0a-05:** If after Phase 1a verification >15% of clusters get `split` or `merge_with` verdicts, re-run with `n_clusters=350` (split-heavy case) or `n_clusters=250` (merge-heavy case). Use the silhouette score and the Cohesion Ratio metric (proposed in the Nov 2025 paper) across k=150..450 to pick the inflection point without refitting — one tree fit handles all k via `scipy.cluster.hierarchy.fcluster`.

### 1.3 GraphRAG's Leiden does not apply

**Confirmed.** [0a-01] confirms from the official config reference and a maintainer's statement that GraphRAG's Leiden operates on entity co-occurrence edges, not raw embeddings, and that its entity merging is exact-name-only. "Lead Gen Channels" and "Lead Generation" would remain separate nodes. Our embedding-cluster + LLM-rename pipeline is the correct pre-pass.

**Nuance from 0a-01:** GraphRAG's "gleanings" loop (CONTINUE prompt + YES/NO gate) recovers 10–20% more entities per chunk. We should consider a single gleaning pass in Phase 3a (see §2.2 below).

### 1.4 Cache minimum for Haiku 4.5 = 4,096 tokens

**Confirmed with additional evidence.** Both [0a-03] and [0a-10] independently verify: Haiku 4.5 minimum cacheable prefix = 4,096 tokens. Below this threshold, `cache_creation_input_tokens` returns 0 and no error is raised. PLAN.md §1's stated threshold of 3,000 tokens will silently fail on every Haiku agent.

**Nuance from 0a-10:** The 4,096-token minimum includes the tool-use overhead (496 tokens for `tool_choice: auto/none`, 588 tokens for `any/tool`). If Phase 3a uses forced tool use, the cacheable content must reach 4,096 tokens _net_ of that overhead. Embedding the full NODE-QUALITY-RUBRIC §A+C, CLAIM-DEFINITION, FEW-SHOT.md, and output schema inline satisfies this floor and is useful content regardless.

### 1.5 Use native structured outputs (`output_config.format` with `json_schema`)

**Confirmed.** [0a-10] confirms GA on Haiku 4.5 as of 2026-02-04. Constrained-decoding grammar makes syntactically invalid JSON impossible. The 24-hour grammar cache amortizes the 100–300ms compilation cost.

**Nuance from 0a-10:** Two requirements that are not yet stated in PLAN.md: (a) Mark _all_ needed fields as `required` — Haiku omits optional fields under token pressure even with structured outputs, since the omission is semantically valid under the schema. (b) Changing `output_config.format` byte-for-byte across concurrent agents is required for cache hits — any per-agent variable content must live in the user message, not in the cached system prompt prefix.

### 1.6 Set `max_tokens` to 1.5× estimated output

**Confirmed.** [0a-10] confirms the failure mode: `stop_reason == "max_tokens"` truncates the output mid-array with no error; the truncated JSON is syntactically valid up to the cut point and will pass schema validation, causing silent data loss. For 60-pair connection labeling (~6k output tokens), `max_tokens=9000`. Monitor `stop_reason` on every response.

**Nuance:** [0a-10] notes that Haiku 4.5's max output is 64,000 tokens, so the 1.5× ceiling never approaches the model limit at PLAN's batch sizes. This is purely a guard against lazy defaults, not a model capacity concern.

---

## §2 — New decisions surfaced by Phase 0a research

These decisions are not yet in PLAN.md and each warrants an ADR.

### 2.1 Chunking: title-prepending with `premise` field is the correct and complete chunking enhancement

[0a-06] evaluated title-prepending, cross-section overlap, Anthropic contextual retrieval, and late chunking. Conclusion: prepend `"{source_title} > {section_title}\n{section.premise}\n\n{chunk_body}"` before embedding. This is a one-line change in the embedding pipeline, zero marginal LLM cost, and captures ~80–100% of what contextual retrieval would generate — because Scribe already extracted `section.premise` in Pass A2.

Cross-section overlap provides no measured benefit for semantically self-contained sections (our A2 sections have `premise` + `conclusion` that make each section standalone). Late chunking (Jina) is inapplicable because full Scribe transcripts exceed the 8,192-token window of available local models.

**Decision:** Apply title+premise prepend at embed time in Phase 2. Do not implement cross-section overlap or the full Anthropic contextual retrieval pipeline. Revisit only if Phase 6 eval shows retrieval failure rate > 5% at section boundaries.

### 2.2 Extraction prompt: use two-pass entity-then-claim design with one gleaning pass

[0a-02] surveyed five production RAG frameworks. Two findings drive a new decision for Phase 3a:

First, GraphRAG separates entity identification (Pass 1) from claim extraction (Pass 2, which takes entity names as inputs). This two-pass dependency reduces hallucinated actors — directly addressing our A1 failure mode (ambiguous actor reference). For Scribe: Pass A of Phase 3a extracts speaker + topic pairs; Pass B extracts claims anchored to those named speakers.

Second, GraphRAG's gleaning loop (CONTINUE prompt + YES/NO gate) recovers 10–20% more entities on longer chunks with only one conditional LLM call per source. A full plan→draft→verify three-stage pipeline adds two extra calls per source (×204 sources = expensive); one gleaning pass adds one conditional call. At Haiku cost, one gleaning pass is the right tradeoff. The YES/NO gate should use logit bias = 100 on those two tokens as GraphRAG does.

**Decision:** Phase 3a uses a two-call structure per source: (a) speaker+topic extraction, (b) claim extraction anchored to named speakers. Add one conditional gleaning pass after (b). Replace the planned "Pydantic-style schema in the prompt + retry" with native structured outputs as per §1.5.

### 2.3 DDD ubiquitous language for third-party sources: add `speaker_term` field and concept-stability checks

[0a-07] established that every PKM and knowledge-base system — Obsidian, Zettelkasten, Wikipedia, BookNLP, SciCo — lacks automatic detection that two differently-named third-party entries refer to the same concept. The closest production analog (SciCo + ODKE+) still fails at "context fixation" on narrow transcript segments.

Two actionable implications:

(a) **`speaker_term` as a first-class field.** Every claim must carry the speaker's original surface-form term alongside the canonical `topic` field. This enables Phase 1b (concept-naming critic) to build an `aliases[]` list that maps each speaker's idiosyncratic vocabulary to the canonical concept handle. Without this field, the alias information is permanently lost after Phase 3a normalization.

(b) **Concept-stability check for incremental ingestion.** The current 98/197 processed sources represent half the corpus. When the second batch (≈99 videos) is ingested, a concept-stability check must be run: for each canonical topic node, compute embedding similarity between the centroid of claims from batch 1 and claims from batch 2. Cosine distance above a threshold (suggest 0.12–0.18 for 1024d Qwen3 embeddings) flags the node for human review. Without this, the graph silently accumulates concept collisions from the second batch.

**Decision:** Add `speaker_term: str | null` to the CLAIM-DEFINITION.md §3 claim shape. Implement `scripts/concept_stability_check.py` before ingesting the second source batch.

### 2.4 Claim definition: add Decontextualization as a first-class positive property

[0a-08] surveyed ClaimBuster, FEVER, SciFact, and the AIDA schema. The single most consistent finding across all pipelines is the **Decontextualization** criterion (AIDA's "Independent"): a claim must be evaluable for truth or falsity by a reader who has _no access to the source document_. Inter-annotator agreement for this property is Gwet's AC1 = 0.85 across four reviewed studies.

Our CLAIM-DEFINITION.md §3 currently says the `text` field "Reads correctly out of context." That is necessary but insufficient. The AIDA-aligned criterion adds: "A reader with no access to the source file must be able to identify what evidence would settle whether the claim is true or false." This stronger property is what makes A1, A3, A10, and A11 failures _derivable_ from the text field itself, rather than requiring 20 separate failure-mode checks.

**Decision:** Update CLAIM-DEFINITION.md §3 to add the Decontextualization property to the `text` field description. This becomes the primary extraction filter: if the claim text does not pass Decontextualization, it is rejected before any rubric row is checked. The 20 failure modes in NODE-QUALITY-RUBRIC §A remain as diagnostic examples, but the positive gate is Decontextualization.

### 2.5 Contradiction detection: add local NLI pre-filter before Phase 4

[0a-09] demonstrated that cosine similarity alone conflates agreement, paraphrase, builds-on, and contradiction at high rates. Two key empirical findings: (a) at cosine ≥ 0.70, the MNLI base rate for neutral pairs (neither agreement nor contradiction) is ~33%; (b) at extreme cases, 100% false positive rate using embedding similarity alone for hallucination detection. Haiku will systematically under-detect contradictions and over-label high-cosine neutral pairs as agreement.

Recommended architecture: `cross-encoder/nli-deberta-v3-base` (200M parameters, 90% MNLI accuracy) as a pre-screen over all 7,800 candidate pairs before Haiku batches. Wall-clock cost on M-series: ~12–33 seconds for 5,000 pairs — negligible. Expected outcomes: drop 20–30% of high-neutral pairs before Haiku (saving ~6 agents or $0.30–0.40), and provide `nli_entailment` and `nli_contradiction` scores in the per-pair Haiku payload as tie-breaking signals.

Routing rules: HIGH entailment (≥ 0.92) AND cosine ≥ 0.60 → hint to Haiku "strong agreement likely"; HIGH contradiction (≥ 0.85) AND cosine ≥ 0.50 → hint "likely contradiction — verify carefully"; HIGH neutral (≥ 0.80) AND cosine < 0.60 → drop before Haiku.

**Decision:** Add NLI pre-filter step to Phase 4 using `cross-encoder/nli-deberta-v3-base`. Add `nli_entailment` and `nli_contradiction` fields to the Phase 4 per-pair payload schema. Update CONN_PROMPT_V2.md to include NLI-hint instructions.

### 2.6 Caching: use 1-hour TTL for phases where agents run > 5 minutes apart

[0a-03] confirmed that 1-hour TTL is available at 2× base write cost ($2/MTok for Haiku 4.5). Break-even is 1.11 reads within the hour. For phases where 30 agents run in parallel within seconds (Phase 4), 5-minute TTL is sufficient and cheaper. For phases where agents may be dispatched sequentially with human review between batches (Phase 1a, Phase 3a), 1-hour TTL ensures the cache write amortizes across a full batch even if dispatch is staggered by up to 55 minutes.

**Decision:** Use 5-minute TTL for Phase 4 (parallel dispatch, well within window). Use 1-hour TTL for Phase 1a (cluster verification, 15 agents with potential stagger) and Phase 3a (extraction, ~40 agents likely dispatched in sub-batches with review). Update PLAN.md §1 cache strategy accordingly.

### 2.7 Per-domain synonym lexicon in CLAIM-DEFINITION §6

[0a-07] §12 recommends expanding the CLAIM-DEFINITION.md §6 "words to watch" table from meta-vocabulary (fact, insight, principle) to include known speaker-specific synonyms in the Scribe corpus. For example: Hormozi's "front-end offer" = general "lead magnet" = others' "entry-point product". These are the application-layer equivalent of Wikipedia's disambiguation pages.

**Decision:** In Phase 1b (concept-naming critic), instruct agents to populate a per-domain synonym sub-table in CLAIM-DEFINITION.md §6 as they review concept names. Each entry: `{speaker_term: str, canonical_term: str, source_speaker: str, note: str}`. This sub-table becomes a persistent artifact updated with each source batch.

---

## §3 — Proposed ADR titles (for Opus to author)

Each proposed ADR satisfies all three ADR-format.md criteria: hard to reverse, surprising without context, result of real trade-offs.

1. **ADR-0001: Chunking enhancement — title+premise prepend, no cross-section overlap** — Explains why we chose the deterministic prepend over Anthropic's contextual retrieval (we already have the data), and explicitly rejects cross-section overlap (neutral-to-negative in 2026 studies for semantically self-contained sections).

2. **ADR-0002: Two-pass extraction with one gleaning pass, not plan→draft→verify** — Explains the entity-first → claim-anchored-to-entities two-pass design and the single gleaning pass. A reader would expect either a single-pass extractor or a three-stage pipeline; the two-pass + one-glean choice is non-obvious.

3. **ADR-0003: `speaker_term` as a required field in the claim schema** — Records that speaker surface forms are captured alongside the canonical topic at extraction time, and why post-hoc alias inference from claim text alone is insufficient (the SciCo context-fixation failure mode).

4. **ADR-0004: Decontextualization as the primary claim gate** — Records the upgrade from "reads correctly out of context" (necessary) to the AIDA-aligned decontextualization property (necessary + sufficient). Explains why this single positive property makes A1/A3/A10/A11 derivable, and cites the Gwet's AC1 = 0.85 empirical support.

5. **ADR-0005: NLI pre-filter (`cross-encoder/nli-deberta-v3-base`) before Phase 4 Haiku batches** — Records the decision to add a local 200M-parameter cross-encoder pre-screen. Explains why cosine ≥ 0.45 alone conflates agreement and contradiction at the MNLI base rate, and why NLI is a _refinement_ of high-sim pairs (not a standalone filter).

6. **ADR-0006: 1-hour cache TTL for Phase 1a and 3a; 5-minute TTL for Phase 4** — Records the TTL selection logic and the 2× write-cost tradeoff. Non-obvious: a reader might default all phases to 5-minute TTL and silently lose cache savings on staggered-dispatch phases.

7. **ADR-0007: Concept-stability check on each source batch ingestion** — Records the decision to add `scripts/concept_stability_check.py` before batch 2. Explains the ontology drift risk from incremental ingestion and the embedding-centroid-movement detection approach.

---

## §4 — Tensions and open issues

### T1: Cosine threshold for Phase 4 pairs (0.45 in PLAN vs research suggesting higher floor)

PLAN.md uses `threshold ~0.45` for candidate pairs. [0a-09] shows that at cosine ≥ 0.45, a large fraction of pairs will be high-neutral (neither agreement nor contradiction). The NLI pre-filter (§2.5) partially addresses this by dropping high-neutral / low-cosine pairs before Haiku. However, raising the floor to 0.55–0.60 would reduce the 7,800 candidate pair count materially and improve edge density without losing many genuine edges.

**Recommendation:** Keep 0.45 as the initial floor but apply the NLI neutral-drop rule (HIGH neutral ≥ 0.80 AND cosine < 0.60 → drop) before Haiku. This effectively raises the functional floor to ~0.60 for neutral pairs while preserving potential contradiction pairs at 0.45–0.60 that cosine would otherwise discard.

### T2: Gleaning loop on transcripts vs. documents (open question from 0a-02)

[0a-02] notes that GraphRAG benchmarked gleaning on news and academic text, not spoken transcripts where entities repeat frequently. It is possible that a YES/NO gleaning gate converges in 0 rounds (no missed entities) on Scribe transcripts because speakers repeatedly name themselves and their companies. If so, the gleaning pass adds cost without benefit.

**Recommendation:** Test the gleaning convergence rate on 5 Scribe sources before committing to `max_gleanings=1` for all 204 sources. If >80% converge in 0 rounds, disable the gleaning loop entirely.

### T3: Ward's equal-cluster-size assumption vs. Zipfian topic distribution

[0a-05] notes that Ward agglomerative assumes similarly-sized clusters but Scribe's topic distribution is likely Zipfian (a few mega-topics like "Lead Generation" + many niche topics). Ward will aggressively split mega-topics. The Phase 1a Haiku verification step will catch pathological splits via the `split` and `merge_with` verdicts, but if >20% of mega-topic clusters get `merge_with` verdicts, switch `linkage='average'` instead of `'ward'` for the final production run.

**Recommendation:** Run Ward at n_clusters=300 for Phase 1a. If the merge_with verdict rate exceeds 20% on the 15-agent verification pass, refit with `linkage='average', metric='cosine'` and re-verify a 20-cluster sample before re-running all 15 agents.

### T4: CLAIM-DEFINITION.md needs two concrete updates before Phase 3a

Two research findings require edits to companion documents, not just the plan:

1. Add `speaker_term: str | null` to the §3 claim shape YAML (from §2.3 above).
2. Upgrade the `text` field description in §3 to include the Decontextualization criterion (from §2.4 above).

These are pre-conditions for Phase 3a; if the extraction prompt quotes CLAIM-DEFINITION.md §3 verbatim (which it should for caching stability), the updates must be in place before any Phase 3a agent is dispatched.

### T5: Diagnostic corpus pass rate (36.8%) is the baseline, not the target

The 239-node corpus has a 36.8% pass rate overall. This is the _current graph_ baseline. The top failure modes are A1 (83/239 nodes, 34.7%) and A3 (78/239 nodes, 32.6%). These two failure modes alone are present in nearly two-thirds of failing nodes. This strongly validates the prioritization of Decontextualization as the primary gate (§2.4): a single decontextualization check would catch A1 (unresolved actor) and A3 (axis-less number) in a single positive test.

Phase 6 quality eval must beat this baseline by ≥25% on overall claim-pass-rate, per PLAN §2. The 36.8% baseline implies Phase 6 must achieve ≥46% to satisfy the minimum bar — a substantial but achievable target given the extraction prompt improvements.

---

## §5 — Follow-up implementation work tagged per phase

### Pre-Phase 1a (before any agents launch)

- [ ] **Update PLAN.md §1:** Change "System prompts ≥ 3000 tokens" → "≥ 4,096 tokens". Note 1-hour TTL for phases 1a and 3a. [0a-03, 0a-10]
- [ ] **Update CLAIM-DEFINITION.md §3:** Add `speaker_term: str | null` field to claim shape YAML. Upgrade `text` field description with AIDA Decontextualization criterion. [0a-07, 0a-08]
- [ ] **Verify Qwen3 prefix:** Run sanity-check embed on top-20 near-duplicate topic strings with instruction prefix before Phase 1a. [0a-04]

### Phase 1a (clustering + verification)

- [ ] **Two-level cut:** Fit one Ward tree, cut at n_clusters=300 for L0 concepts and n_clusters=50 for super-concepts. Map `children_` → `parent_id` in `knowledge/concepts.json`. [0a-05]
- [ ] **Feedback gate:** If >15% `split`/`merge_with` verdicts → refit at n_clusters=350 or 250 and re-verify 20 clusters before running all 15 agents. [0a-05, T3]

### Phase 1b (concept-naming critic)

- [ ] **Populate speaker synonym sub-table** in CLAIM-DEFINITION.md §6 as agents review concept names. One entry per known speaker-specific synonym variant. [0a-07]

### Phase 2 (re-embed)

- [ ] **Apply title+premise prepend** before all chunk embeddings: `f"{source_title} > {section_title}\n{section.premise}\n\n{chunk_body}"`. [0a-06]

### Phase 3a (extraction v2)

- [ ] **Two-pass extraction:** Pass A extracts speaker+topic pairs; Pass B extracts claims anchored to those named speakers. Add one conditional gleaning pass. [0a-02]
- [ ] **Use native structured outputs** (`output_config.format` with `json_schema`). All fields required (no optional fields). Flatten nested schemas. [0a-10]
- [ ] **Verify Haiku system prompt ≥ 4,096 tokens** (including tool overhead). Check `cache_creation_input_tokens > 0` on first agent. [0a-03, 0a-10]
- [ ] **Set `max_tokens` = 1.5× estimated output** per batch size. Monitor `stop_reason` on all responses. [0a-10]
- [ ] **Use 1-hour TTL** for Phase 3a cache writes. [0a-03, §2.6]

### Phase 4 (connection rebuild v2)

- [ ] **NLI pre-filter:** Run `cross-encoder/nli-deberta-v3-base` over all candidate pairs before Haiku batches. Add `nli_entailment` and `nli_contradiction` fields to per-pair payload. [0a-09]
- [ ] **Update CONN_PROMPT_V2.md** with NLI-hint routing rules. [0a-09]
- [ ] **Drop rule:** HIGH neutral (≥ 0.80) AND cosine < 0.60 → drop before Haiku. [0a-09, T1]
- [ ] **Use 5-minute TTL** for Phase 4 cache writes (parallel dispatch). [§2.6]

### Post-Phase 4 / incremental ingestion

- [ ] **Implement `scripts/concept_stability_check.py`** before second source batch is ingested. [0a-07, §2.3]

---

## §6 — Readiness check

### Ready to execute now (Phase 1a clustering + verification)

The following are fully resolved and implementation-ready:

1. **Embedding model selected:** `qwen3-embedding:0.6b`. Pull command known. Instruction prefix known. Sanity check specified. [0a-04]
2. **Clustering algorithm selected:** Agglomerative Ward, L2-normalized, n_clusters=300, scipy fcluster for multi-level cuts. Reference implementation code is in [0a-05]. [0a-05]
3. **Cluster verification prompt structure:** FEW-SHOT.md is complete (10+/20−). NODE-QUALITY-RUBRIC.md and CLAIM-DEFINITION.md are final modulo the two pre-Phase-1a edits listed in §5. [FEW-SHOT.md]
4. **Phase 1a agent cost confirmed:** ~$0.050/agent × 15 agents = $0.75. Cache math confirmed for 4,096-token minimum. [0a-03, 0a-10]
5. **Output format:** `knowledge/concepts.json` with `canonical_name`, `aliases[]`, `members[]`, `super_cluster_id`, `parent_id`. [0a-05, HIERARCHY.md]

**Immediate blockers to clear before launching Phase 1a:**
- Update PLAN.md §1 cache threshold from 3,000 → 4,096 tokens.
- Add `speaker_term` field and Decontextualization criterion to CLAIM-DEFINITION.md §3.
- Run the Qwen3 near-duplicate sanity check.

### Not yet ready (later phases)

- **Phase 3a extraction prompt:** Two-pass design (§2.2) and structured-output schema for the claim shape need a fresh brief. The Phase 3a brief in PLAN.md currently references "Pydantic-style schema in the prompt + retry" — this must be replaced with the native structured-output approach before any Phase 3a agent is dispatched.
- **Phase 4 NLI pre-filter:** `cross-encoder/nli-deberta-v3-base` needs to be downloaded and tested for throughput on M4. CONN_PROMPT_V2.md does not yet exist; it must be written to include NLI-hint routing rules.
- **CONN_PROMPT_V2.md:** Not yet written. Must include: NLI hint interpretation rules, the five `kind` values with examples, the `agreement_axis` field for agreement/contradiction cases, and the sentence template for the `sentence` field.
- **Concept-stability check script:** Not yet implemented. Needed before the second source batch is ingested (~99 pending videos).
- **ADR files:** Seven ADRs proposed in §3. Opus task to author them in `docs/adr/`. None blocks Phase 1a.
- **`scripts/concept_stability_check.py`:** Not yet implemented. Needed before second batch of sources. Does not block Phase 1a–4.
