# Graph Rebuild — Plan

This is the durable plan for rebuilding the Scribe knowledge graph. Every sub-agent dispatched during this rebuild reads the appropriate slice of this document plus its phase-specific brief. Read this file end-to-end before launching any phase.

Companion documents (read these first):
- [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) — every failure mode catalogued, plus the scoring matrix every node must pass.
- [CLAIM-DEFINITION.md](./CLAIM-DEFINITION.md) — what a Claim is, what it isn't, and the canonical ubiquitous language for our node types.
- [HIERARCHY.md](./HIERARCHY.md) — the L0 Concept / L1 Framework / L2 Claim / L3 Example structure.
- [skills/ubiquitous-language.md](./skills/ubiquitous-language.md), [skills/context-format.md](./skills/context-format.md), [skills/architecture-language.md](./skills/architecture-language.md), [skills/adr-format.md](./skills/adr-format.md), [skills/diagnose.md](./skills/diagnose.md) — verbatim mattpocock/skills sources we are borrowing.

## §−2 — Constraint shift (2026-06-16, mid-execution)

User signaled that **usage budget, not dollar cost, is the limiting factor**. Their account usage went 18% → 34% across Phase 0. Target finish at 80-90% (~50% more headroom).

Plan revisions:

- **Drop the local NLI pre-filter** (synthesis §2.5). The cross-encoder/nli-deberta-v3-base was a cost optimization — with usage as the true constraint, Haiku 4.5 directly judges every candidate pair without a local pre-screen. We avoid local-model latency, model-download overhead, and a separate failure surface; we trade ~$0.40 saved per the synthesis for hundreds of additional Haiku tokens that we now have headroom to spend.
- **Promote selective Haiku → Sonnet 4.6** for high-judgment calls: Phase 1b concept naming, Phase 1a "hard clusters" tiebreaker, Phase 3a per-source second-opinion judge, Phase 4 ambiguous-edge tiebreaker, Phase 6 final auditor. Sonnet stays the minority — Haiku handles the bulk extraction; Sonnet handles the synthesis/audit/disagreement-resolution layers.
- **Add cross-validation** to extraction-heavy phases: Phase 3a runs each source through 1 Haiku extractor + a Sonnet "second opinion" gate that compares the extraction against the rubric and either approves or sends back with annotated rejections. This ~doubles extraction work but materially improves the floor.
- **Scale agent counts** upward where parallelization buys quality: Phase 1a 15 → 20 Haiku + 3 Sonnet judges; Phase 3a 40 → ~204 Haiku (one per source) + ~20 Sonnet seconds-opinion; Phase 4 30 → ~40 Haiku (full candidate set, no pre-filter); Phase 6 8 → 16 Haiku + 4 Sonnet auditors.
- **Goal hierarchy:** maximize accuracy of extracted claims and graph connections > minimize agent count.

Estimated revised agent total: ~310 (vs ~110 in original §1). Token spend ~3× Phase 0.

## §−1 — Phase 0a findings that revise this plan

These decisions are fixed by Phase 0a research (see `research/0a-research-NN.md` files for sources). They supersede earlier hedges:

- **Embedding model = `qwen3-embedding:8b`** (primary), `qwen3-embedding:4b` (fallback if RAM-bound). 8B Qwen3 tops MTEB at 70.58 overall; clustering subscore is ~58+ (extrapolated from the 4b's 57.15 and the overall delta). 32K context window (handles long claims without truncation). 4.7 GB download. ~12–18 min full embed on M4 for ~15k items. Upgraded from 0.6b per user's "avoid smaller local models" directive (§−2). *[0a-04]*
- **Clustering algorithm = Agglomerative Ward** on L2-normalized embeddings, not HDBSCAN. Direct `n_clusters=300` parameter. Full dendrogram for free → cut at `n_clusters=50` for super-concepts. No noise/orphan points (HDBSCAN produces 30–60% noise on short labels). One fit, multiple cuts via `scipy.cluster.hierarchy.fcluster`. *[0a-05]*
- **GraphRAG's Leiden does not apply.** It clusters an entity co-occurrence graph, not raw embeddings, and merges only exact-name duplicates. Our Phase 1a/1b pipeline (embedding clusters + LLM rename) is correct and necessary. *[0a-01]*
- **Cache minimum threshold for Haiku 4.5 is 4,096 tokens, NOT 3,000.** Below 4,096 the cache silently misses with no error. All Haiku agent system prompts must be padded to ≥4,096 tokens via inline rubric/claim-def/few-shot content (which is useful anyway). Sonnet 4.6 minimum is 1,024 tokens — Sonnet agents can cache smaller prompts. *[0a-10]*
- **Use native structured outputs** (`output_config.format` with `json_schema`), GA on Haiku 4.5 as of 2026-02-04. Eliminates the JSON-syntax retry loop in Phase 3a/4. Keep all fields required (Haiku omits optional fields under token pressure). Flatten nested schemas. *[0a-10]*
- **Set `max_tokens` to 1.5× estimated output** to prevent silent truncation. For 60-pair connection labeling (~6k expected output), use `max_tokens=9000`. Monitor `stop_reason` on every response. *[0a-10]*

Other 0a findings (chunking, extraction prompts, claim-extraction prior art, contradiction detection, DDD discipline) will be folded into the Phase 0 synthesis doc once Phase 0c completes.

## §0 — Why this rebuild

The current graph is **fragmented at the concept layer**: 204 sources produced 1472 unique topic strings, 1440 of them appearing in only one source. Universal concepts like Lead Generation are shattered into ~47 near-duplicate single-source nodes. The Haiku connection passes operate over topic strings, so they cannot merge those duplicates — they can only edge between them. Result: the most important nodes have the fewest connections.

Beyond the topology, the **node content itself is low-quality**: ambiguous actor references ("the founder did X"), specific-but-meaningless detail lists, decontextualized numbers, vague blanket statements, atomized bullets that destroy single ideas, advice-as-claim, survivorship bias. The NODE-QUALITY-RUBRIC catalogues twenty distinct failure modes observed in the current data.

The rebuild therefore tackles three layered problems in order:

1. **Topology** — collapse 1472 topic strings to 200-350 canonical concepts (Phase 1).
2. **Hierarchy** — promote Frameworks above Claims above Examples, instead of one flat layer (Phase 2-3).
3. **Quality** — every node passes the rubric's scoring matrix (Phase 3-4).

We do this on the `graph-rebuild` branch. Old `main` keeps the current graph available for A/B comparison until we've validated the rebuild against the quality eval (Phase 6).

## §1 — Cost and concurrency model

All extraction/labeling agents are **Haiku 4.5**. Web research and complex critic agents are **Sonnet 4.6**.

### Haiku 4.5 pricing (as of repo date)
- Input (non-cached): $1 / MTok
- Output: $5 / MTok
- Cache read: $0.10 / MTok (10% of base)
- Cache write: $1.25 / MTok (1.25× base)
- Cache TTL: 5 minutes default, 1 hour available

### Cache strategy
- System prompts ≥ **4,096 tokens** (Haiku 4.5 minimum; Sonnet is 1,024) that are shared by 3+ concurrent agents → mark as cached. Below 4,096 → silent miss with no error. Pad with inline rubric/claim-def/few-shot/schema as needed.
- Per-batch payload (the actual data the agent processes) is fresh each call. Variable per-agent content stays in the user message, not in the cached prefix.
- TTL selection: **5-minute** for parallel-dispatch phases (Phase 4); **1-hour** ($2/MTok write) for phases with staggered dispatch (Phase 1a, Phase 3a) — break-even after 1.11 reads within the hour.
- Verify cache hits via `cache_creation_input_tokens > 0` on first agent's response.

### Optimal Haiku batch sizing (empirical anchors)

| Use case | Pairs/items per batch | Why |
|---|---|---|
| Cluster verification (24-32 clusters of ~5 members each) | ~25 clusters | Per-batch payload ~6k tokens; output ~3k. Lots of cache hits across batches. |
| Connection labeling (CONN_PROMPT v2) | ~60 pairs | Per-batch payload ~15k tokens, output ~6k. Beyond 80 pairs, output JSON reliability drops. |
| Node-quality scoring (single node → score + recommendation) | ~30 nodes | Each node 200-400 tokens; output 100 tokens. |
| Extraction v2 (per-source topic + claims + frameworks) | 1 source per agent (variable size) | One transcript per agent. Long transcripts get sub-windowed inside the agent as in current process.py. |

### Per-agent cost envelope

A representative connection-labeling batch:
- System prompt (cached after 1st write): 4k tokens → write once 4k × $1.25/M = $0.005; subsequent reads 4k × $0.10/M = $0.0004
- Per-batch input: 15k tokens × $1/M = $0.015
- Per-batch output: 6k tokens × $5/M = $0.030
- **Per agent: ≈$0.045** (≈$0.046 first agent, ≈$0.045 subsequent).

A diagnostic agent reading 25 nodes:
- System prompt cached: ~$0.0004
- Input: 8k × $1/M = $0.008
- Output: 3k × $5/M = $0.015
- **Per agent: ≈$0.024**

A Sonnet web-research agent:
- Input (a fetched page summary loop): ~30k × $3/M = $0.090
- Output: ~2k × $15/M = $0.030
- **Per agent: ≈$0.12**

### Total budget across phases (committed)

| Phase | Type | Agents | Per agent | Subtotal |
|---|---|---|---|---|
| 0a — Web research | Sonnet | 10 | $0.12 | $1.20 |
| 0b — Diagnostic node sampling (200-300 nodes) | Haiku | 12 | $0.024 | $0.29 |
| 0c — Few-shot harvest (10+/20−) | Haiku | 4 | $0.040 | $0.16 |
| 1a — Cluster verification | Haiku | 15 | $0.050 | $0.75 |
| 1b — Concept-naming critic | Haiku | 6 | $0.030 | $0.18 |
| 2 — Re-embed (local ollama) | — | 0 | $0 | $0 |
| 3a — Extraction v2 (~204 sources) | Haiku | ~40 (5 per agent) | $0.060 | $2.40 |
| 3b — Hierarchy classifier | Haiku | 12 | $0.030 | $0.36 |
| 4 — Connection rebuild v2 | Haiku | 30 | $0.045 | $1.35 |
| 6 — Quality eval | Haiku | 8 | $0.030 | $0.24 |
| **Total** | | **~137 agents** | | **≈$6.93** |

Headroom: budget $15 to cover retries, prompt iteration, and one full re-run of Phase 3 if the first pass misses the rubric cutoffs.

## §2 — Phases

### Phase 0a — Web research (parallel, 10 Sonnet agents)

Goal: ground every downstream technical decision in current external evidence, rather than my parametric knowledge. Each agent gets one tightly-scoped question and a budget of ~5 fetches.

Agents (10):

1. **GraphRAG entity canonicalization.** "How does Microsoft GraphRAG canonicalize entity names across documents? Read the GraphRAG paper + indexing-engine repo + recent blog posts. What clustering threshold do they recommend for entity merging? Do they use Leiden / Louvain / HDBSCAN for community detection?"
2. **LangChain / LlamaIndex topic-extraction prompts.** "What system prompts do production RAG frameworks (LangChain, LlamaIndex, Haystack) use for entity & topic extraction from long transcripts? Capture 3-5 verbatim prompts. What are their few-shot patterns?"
3. **Anthropic prompt-caching docs.** "Read the current Anthropic docs on prompt caching. Confirm: cache TTL options, minimum-token cacheable size, pricing as of latest update, how caches interact with structured-output / tool-use. Surface anything that changes our cache strategy."
4. **Short-text embedding benchmarks.** "What is the current best local embedding model for short-text (2-4 word topic labels) clustering and retrieval? Cover MTEB leaderboard top entries that are ollama-available: nomic-embed-text v1.5, mxbai-embed-large, bge-large-en-v1.5, snowflake-arctic-embed. Recommend with reasoning."
5. **Hierarchical clustering at scale.** "For clustering ~1500 short-label embeddings into 200-400 clusters with parent-child structure, what algorithm gives best quality? HDBSCAN vs agglomerative vs BERTopic vs OpenAI's clustering recipe. Threshold tuning advice. Citation."
6. **Section-aware chunking best practices.** "What is the state of the art on chunking long transcripts for RAG, given we already have LLM-extracted section boundaries? Should we overlap, prepend titles, use late-chunking, or contextual chunking (Anthropic's recent post)? Read recent Anthropic, LangChain, LlamaIndex pieces from 2025-2026."
7. **DDD ubiquitous language for non-software domains.** "How is DDD's ubiquitous-language concept being applied outside of software — to knowledge bases, encyclopedias, or research notes? Find examples and patterns. Identify what changes when the 'glossary' is built from third-party source material vs. being authored by a team."
8. **Claim extraction & verification.** "Survey current research on claim extraction from natural language — fact-checking pipelines, FEVER, SCIVER, MULTIVERS. How do they distinguish a claim from a non-claim? What schemas do they use? What is the inter-annotator agreement on 'is this a claim'?"
9. **Cross-source agreement and contradiction detection.** "How do knowledge-graph and fact-check systems detect when two sources agree, build on, or contradict each other? What signals do they use beyond surface embedding similarity? Are there off-the-shelf models (NLI, FactCC, etc.) cheap enough to run locally on 5000+ claim pairs?"
10. **Haiku 4.5 prompt-engineering tips.** "Read recent Anthropic posts on Haiku 4.5 specifically. How does it differ from Sonnet for structured-extraction tasks? What batch sizes do practitioners report as optimal? Any known failure modes? Any specific structured-output tricks (JSON-mode, tool-use)."

Each agent writes its findings to `.scribe-skills/research/0a-research-NN.md`. Phase 0 closes when all 10 are in. A synthesizing **Sonnet** call (one more, not counted above, ~$0.20) reads all 10 + this PLAN.md and writes `.scribe-skills/research/0a-SYNTHESIS.md` summarizing the actionable decisions and any plan revisions.

### Phase 0b — Diagnostic node sampling (12 Haiku agents)

Goal: produce the few-shot training corpus by labeling 200-300 real nodes from the current graph against NODE-QUALITY-RUBRIC. Each agent labels ~20 nodes.

Sampling:
- **Stratified sample of 240 nodes:** 60 from `knowledge/topics/*.md` (claim-bearing notes), 60 from `connections.json` edges (cross-source pairs), 60 from `sources.json` `video_summary` fields, 60 from the multi-source topic strings (the 32 that already appear in 2+ sources, plus their high-confidence sibling clusters).
- **Stratify by surface failure-mode signals** so we get coverage: pick ~30 candidates per Section-A failure mode using grep heuristics (e.g. "the founder" → A1; bullets matching `^- \w{1,5}$` → A5/A6; standalone integers in claim text → A3).

Per agent (20 nodes):
- System prompt: NODE-QUALITY-RUBRIC.md Sections A + C + a 3-shot mini-example.
- Input: 20 candidate nodes (~250 tokens each = 5k tokens) + their source-file context line (~50 tokens each = 1k tokens).
- Output: per node — `{ node_id, scores: {1..7}, failure_modes: [A1, A4, ...], proposed_action: keep/rewrite/demote/drop, rewritten_text?: "..." }`. About 250 tokens per node = 5k output.
- **Per agent cost: ~$0.024.**

The 12 outputs are merged into `.scribe-skills/research/0b-diagnostic-corpus.jsonl`.

### Phase 0c — Few-shot harvest (4 Haiku agents)

Goal: synthesize the labeled corpus down to the canonical **10 positive + 20 negative** few-shot examples that every downstream extraction agent will see.

- 1 agent picks the **10 positive** examples — one per positive signal from NODE-QUALITY-RUBRIC §B, plus four "exemplary load-bearing claims" with mechanism + axis + conditions + counterexample.
- 3 agents pick the **20 negative** examples — must cover **all 20 failure modes from §A**, with at least one egregious example per mode. The three agents propose candidates; a meta-step (handled inline by my next message, no new agent) picks the best one per mode and removes duplicates.

Output: `.scribe-skills/FEW-SHOT.md`, embedded by reference into every extraction prompt.

### Phase 1a — Topic clustering + verification (15 Haiku agents)

Goal: collapse 1472 topic strings → 200-350 L0 Concepts using HDBSCAN (or alternative selected in Phase 0a research) on embeddings of `f"{topic_string}. {one-line-headline}"` produced by the embedding model recommended in Phase 0a.

Mechanical pre-pass (no LLM):
1. Embed all 1472 topic strings with the selected model.
2. Run clustering. Tune threshold / `min_cluster_size` until the cluster count lands in the 200-350 band and visual inspection (a random sample of 20 clusters) shows tight semantic coherence.
3. For each cluster, compute the centroid and emit `{cluster_id, members: [topic_string], proposed_canonical: <member nearest centroid>, headlines: [...]}`.

Haiku verification (15 agents, ~22 clusters each):
- System prompt = NODE-QUALITY-RUBRIC §B + CLAIM-DEFINITION §1-3 + FEW-SHOT.md.
- Per cluster, agent emits one of:
  - `accept`: the proposed canonical name and member set are good.
  - `rename`: keep members, change canonical name to one of the members or a synthesis of them.
  - `kick`: remove specific members that don't belong (they go back into the candidate pool for re-clustering).
  - `split`: this cluster is two concepts; emit two `{name, members[]}` pairs.
  - `merge_with`: this cluster should merge with cluster N (named).
- Output per cluster ≈ 200 tokens → 4-5k tokens per agent.
- **Per agent cost: ~$0.050.**

Outputs merge into `knowledge/concepts.json` (the new authoritative concept list with aliases).

### Phase 1b — Concept-naming critic (6 Haiku agents)

After 1a settles, the canonical concept names are reviewed for the ubiquitous-language discipline (mattpocock/skills/ubiquitous-language.md):
- Be opinionated. Same concept ≠ two names.
- Tight, one-sentence definition.
- Aliases listed.
- Ambiguities flagged.

Each agent reviews ~50 concepts, applies the discipline, writes back to `knowledge/concepts.json`.

### Phase 2 — Re-embed with chosen model (local, ~40 min)

Once Phase 0a recommends the embedding model, re-embed:
- chunks collection (50/source × 204 sources = ~10k chunks)
- facts collection (50/source × 204 sources = ~10k facts — but Phase 3 will rewrite facts so we may defer this until after Phase 3)

Wall-clock dominant; no agent cost. Done with `process.py --reembed`.

### Phase 3a — Extraction v2 (~40 Haiku agents)

Goal: re-extract `topics`, `claims`, `frameworks`, `examples`, `practices` from every transcript using the new ubiquitous language + NODE-QUALITY-RUBRIC. Producing nodes that score 5+ on the rubric.

Each agent processes 5 sources (one batch of 5 transcripts). Workflow per source per agent:

1. Read transcript file (already chunked into sections by the existing Pass A — reuse those section boundaries; do not re-section).
2. For each section:
   - Identify which **L0 concepts** from `knowledge/concepts.json` are addressed (multi-label).
   - Extract candidate **L1 frameworks** mentioned by name.
   - Extract candidate **L2 claims** that score 5+ on the rubric. Each claim carries: text, type, attribution (speaker, transcript_offset), conditions[], mechanism?, numbers?, bounded_by[].
   - Extract candidate **L3 examples** as children of claims.
   - Extract **practices[]** (imperatives without a backing claim) separately.
3. Emit JSON per source to `.haiku/v2_extracted/<source_id>.json`.

System prompt: NODE-QUALITY-RUBRIC §A + §C + FEW-SHOT.md + CLAIM-DEFINITION §3 (the claim shape).

Constraints in the prompt:
- "When in doubt, demote. A claim that doesn't score 5/5 must become an example, a practice, or be dropped. Do not lower the bar."
- "Use the L0 concept names from `concepts.json` verbatim. Do not invent new concepts. If a candidate concept doesn't exist, mark it `unmapped` for the post-pass — do not silently create."
- Output schema is enforced with a Pydantic-style schema in the prompt and rejected JSON triggers a retry.

Cost: ~$0.060 × 40 agents = $2.40.

### Phase 3b — Hierarchy classifier (12 Haiku agents)

After extraction, each candidate node from Phase 3a is classified into one of {L0, L1, L1', L2, L2a, L2b, L3, L3a, L4, L4'} per HIERARCHY.md. Multi-source claim duplicates are merged here (paraphrase detection via embedding similarity threshold ~0.85, then LLM tie-break).

Each agent processes ~500 candidate nodes from the merged extraction pool. Output: `knowledge/v2/nodes.jsonl`.

### Phase 4 — Connection rebuild v2 (30 Haiku agents)

Now the canonical concepts (~250) and the verified claims (~5000) are stable. We compute connections:

Candidate pairs (mechanical):
- For each canonical concept, top-K=12 semantic neighbors via the new embeddings with threshold ~0.45.
- For each claim, top-K=8 semantically-similar claims **on other concepts** (intra-concept claim similarity is uninteresting — they are already linked through their concept parent).
- Demote same-source pairs in the orphan_candidates style.
- Total candidate pairs estimated: ~1800 concept-concept + ~6000 claim-claim ≈ 7800 pairs. 30 agents × 260 pairs each ≈ 8000 cap.

Per-batch payload structure (the agent sees these):
```json
[
  {
    "a_id": "claim-1234", "b_id": "claim-5678",
    "a_text": "<rewritten claim text>",
    "b_text": "<rewritten claim text>",
    "a_concept": "Lead Generation",
    "b_concept": "Customer Acquisition",
    "a_source": "...", "b_source": "...",
    "embedding_sim": 0.71
  },
  ...
]
```

System prompt: NODE-QUALITY-RUBRIC §A11-A12 (attribution / contradictions) + CLAIM-DEFINITION §4 (why we include) + FEW-SHOT.md + a new CONN_PROMPT_V2.md.

Output per pair:
```json
{
  "a_id": "...", "b_id": "...",
  "kind": "agreement | builds-on | contradiction | related | none",
  "confidence": 0.0-1.0,
  "sentence": "<one sentence stating the connection, names both speakers/sources, references the specific claim content — no generic 'both about sales'>",
  "agreement_axis": "<for agreement: the specific claim they agree on; for contradiction: the specific axis they disagree along>"
}
```

If `kind: none` or `confidence < 0.5`, drop the edge.

Cost: 30 × $0.045 = $1.35.

### Phase 5 — Render the new graph (mechanical)

Rewrite `export_graph.py` (call it `export_graph_v2.py` so old still works):
- Reads `knowledge/concepts.json`, `knowledge/v2/nodes.jsonl`, `connections_v2.json`.
- Produces `graph/graph_v2.json` with nodes carrying `level` (L0..L4), `parent_id`, `aliases`.
- Update `graph/graph.js` to read level + parent_id and apply a hierarchical force layout (or radial-tree within each concept).
- A query parameter `?graph=v2` switches viewer to new graph. `?graph=v1` keeps old. Default = `v2` on this branch.

### Phase 6 — Quality eval (8 Haiku agents)

Build the **deterministic graph-quality feedback loop** per skills/diagnose.md §Phase 1. The loop is a script: `scripts/eval_graph.py <graph_path>` that:

1. Sanity stats: node count by level; edge count by kind; orphan-node count (no in-edges and no out-edges); fragmentation index (mean nodes per concept).
2. Random sample N=200 claim nodes; for each, format `{text, attribution, conditions, mechanism, numbers, bounded_by}` and emit to a Haiku scoring agent (8 agents × 25 nodes each).
3. Each scoring agent applies NODE-QUALITY-RUBRIC §C and emits a 7-row score. Cutoffs:
   - ≥80% of L2 claim nodes score 5/5 on rows 1-5.
   - ≥90% of L2b quantified-axis nodes score on row 6.
   - 100% of sampled nodes score on row 7 (attribution).
4. Run `eval_graph.py` on v1 first as a baseline. v2 must beat v1 on every metric, by at least 25% on overall claim-pass-rate.
5. If v2 fails the cutoff: write `.scribe-skills/research/6-eval-fail.md` with the failing rows + sample nodes, and we iterate Phase 3a with sharper few-shots before rerunning Phase 4-5.

Cost: 8 × $0.030 = $0.24.

## §3 — Re-runs

A re-run of any phase reads the existing artifacts and only generates what is missing or stale. Each phase has a `--force` flag.

## §4 — What we explicitly are NOT doing yet

- **Switching embedding model in the chat / RAG path** until Phase 6 passes. The chat layer keeps reading the v1 chroma indexes during the rebuild so the chat page is unaffected.
- **Touching the Copy view changes (50/50 RAG bump + per-source summary cards)** until the graph rebuild is validated. They are independent. We can ship them on a small parallel branch if you want, but the rebuild is the priority.
- **Re-chunking the transcript layer.** The existing LLM-titled sections from Pass A2 are already much better than fixed-length chunking — they are the right unit. We add title-prepending and a small overlap (Phase 0a #6 may revise this).

## §5 — Open questions for the user before launching Phase 0

1. **Concept count target.** Aggressive (200, denser graph but risk of merging genuinely-distinct ideas) or conservative (350, looser but safer)? Default = **300** ± 50.
2. **Embedding model.** Default to Phase 0a recommendation, but if you have a prior preference (mxbai-embed-large vs bge-large-en-v1.5 vs nomic-embed-text-v1.5 with Matryoshka 256-d), say so.
3. **Hierarchy depth visibility in the viewer.** Should the graph viewer (a) show all L0..L4 nodes at once with size encoding the level, (b) start at L0 + L1 and let the user drill into a concept to reveal its claims, or (c) toggle? Default = **(b)** with a `Show all levels` toggle.
4. **Cross-source contradiction surfacing.** Contradictions are the most informative edges. Should they be **visually distinct** (red dashed) in the viewer? Default = **yes**, plus a "Contradictions only" filter in the controls panel.
5. **OK to spend ~$7 of API on the full rebuild?** Headroom budget $15.

When you approve §5, I launch Phase 0a (10 Sonnet agents) and Phase 0b (12 Haiku agents) **in parallel**. Phase 0c runs after 0b. Phase 1+ waits on the Phase 0a synthesis to confirm the embedding model and clustering algorithm choice.
