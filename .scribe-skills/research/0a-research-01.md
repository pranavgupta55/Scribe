# 0a-01 — GraphRAG entity canonicalization

## Bottom line

GraphRAG uses the **Leiden algorithm** (via the `graspologic` library) applied hierarchically to produce community levels L0 (root, most abstract) through L2+ (progressively specific). The only exposed clustering knob is `max_cluster_size` (default **10**); there is no resolution parameter in the official config. Critically, entity merging is **exact-name-only**: "Lead Gen Channels" and "Lead Generation" remain separate nodes — GraphRAG does not resolve near-duplicate string variants. For Scribe's 1472→300 collapse, this means we cannot rely on GraphRAG's approach and must do our own pre-pass (embedding similarity + LLM rename) before running any graph algorithm.

## Evidence

- **[arXiv 2404.16130 abstract](https://arxiv.org/abs/2404.16130)** — Confirms the two-stage index: entity knowledge graph from source docs, then hierarchical community summaries via Leiden, generated bottom-up. No entity resolution step described.
- **[GraphRAG YAML config reference](https://microsoft.github.io/graphrag/config/yaml/)** — `cluster_graph` section exposes only: `max_cluster_size` (int, default 10), `use_lcc` (bool), `seed` (int). No resolution, no min_community_size, no max_levels parameter.
- **[GitHub Discussion #683 — "Is GraphRAG community detection parameterless?"](https://github.com/microsoft/graphrag/discussions/683)** — Maintainer confirms it uses "the hierarchical implementation from graspologic." The single env var is `GRAPHRAG_MAX_CLUSTER_SIZE=10`. Hierarchy depth is not user-configurable; it emerges from the graph structure recursively.
- **[Bertelsmann Tech: How Microsoft GraphRAG Works Step-by-Step](https://tech.bertelsmann.com/en/blog/articles/how-microsoft-graphrag-works-step-by-step-part-12)** — Explicitly states: "GraphRAG does not handle entity disambiguation (e.g. _Jon_ and _Jon Márquez_ will be separate nodes despite referring to the same individual)." Deduplication is exact-title grouping only; "deduplication is sometimes imperfect." Hierarchy: L0 = root communities (most abstract), L1/L2+ = sub-communities. A sample corpus produced 15 root-level L0 communities.
- **[Memgraph blog — How Microsoft GraphRAG Works with Graph Databases](https://memgraph.com/blog/how-microsoft-graphrag-works-with-graph-databases)** — Corroborates: entity merging (`Merge` function) consolidates descriptions for entities with **identical names** only. Near-duplicate variants (different strings, same concept) are not merged.

## What this implies for Scribe's graph rebuild

- **Phase 1a clustering algorithm**: Do not adopt GraphRAG's Leiden-on-graph approach. Our input is 1472 raw topic strings (not an entity graph), so we must cluster on **embedding similarity** first (HDBSCAN or agglomerative — see 0a-05 for the choice). Leiden is appropriate only after the graph exists, i.e., after Phase 4 connection rebuild, not as the Phase 1a mechanism.
- **Near-duplicate canonicalization is a pre-pass, not a byproduct**: GraphRAG's exact-name merge means "Lead Gen Channels" and "Lead Generation Volume" would stay split. We need an explicit LLM rename step in Phase 1b (concept-naming critic) that maps all alias strings to a single canonical name before any graph is built.
- **Hierarchy depth**: GraphRAG's Leiden produces ~2–3 usable levels from a typical corpus; our target L0–L4 is deeper and semantically richer. Our hierarchy is type-differentiated (Concept / Framework / Claim / Example), not purely structural clustering — so GraphRAG's community levels are not analogous to our L0..L4 levels.
- **`max_cluster_size=10` is a warning**: GraphRAG caps clusters at 10 entities per community by default. With 300 target concepts and ~5 claims each, our Phase 1a min_cluster_size should be tuned to produce 200–350 groups, not 10-member caps. The Phase 0a research agent #5 (hierarchical clustering at scale) should confirm the right `min_cluster_size` / `min_samples` values for HDBSCAN on 1472 embeddings.

## Open questions

GraphRAG's entity extraction uses "gleanings" (up to N re-prompts per chunk to catch missed entities) — worth checking whether our Phase 3a extraction prompt should adopt the same multi-pass gleaning loop to reduce missed frameworks/claims per transcript section.
