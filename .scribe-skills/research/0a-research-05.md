# Phase 0a Research #05 — Hierarchical Clustering for ~1500 Short-Label Embeddings

**Question:** For clustering ~1500 short-label embeddings into 200-400 clusters with parent-child structure, what algorithm gives best quality in 2026? Compare HDBSCAN, agglomerative (Ward), BERTopic, GraphRAG's Leiden, and OpenAI's recipe.

---

## Summary Verdict (up front)

**Recommended approach for this project: two-pass hybrid — Agglomerative (Ward/average linkage, cosine distance) for the initial flat 200-350 cluster cut, followed by a second cut at ~50 for L0 super-concepts.**

HDBSCAN gives better semantic purity on noisy natural-language text but is *structurally unfit* for the hard constraint of landing in a 200-400 band — enforcing that band requires complex workarounds that introduce new failure modes. Ward agglomerative lets you set `n_clusters=300` directly, produces a dendrogram for free at every granularity level, and achieves O(n²) complexity which is fine at n=1500. BERTopic is a pipeline wrapper around these two, not a distinct algorithm. Leiden (GraphRAG) operates on an entity co-occurrence graph, not on raw embeddings — wrong input representation for this use case. OpenAI's cookbook recipe uses k-means directly on full-dimension embeddings — fast but blind to hierarchy.

---

## (a) Which Algorithm Produces the Cleanest Semantic Clusters for Noisy Short Labels?

### HDBSCAN
- Strongest on *semantically heterogeneous* data with variable-density regions. It builds a minimum-spanning-tree over point densities and extracts stable clusters, labeling low-density points as noise (-1) [1].
- The UMAP → HDBSCAN pipeline is the de-facto standard for short-text clustering: UMAP compresses the high-dimensional embedding space to 5-15 components while preserving both local and global topology; HDBSCAN then clusters in that reduced space.
- A 2021 IEEE study on short-text clustering with UMAP+HDBSCAN found the combination outperformed KMeans and LDA on noisy, variable-length short text [2].
- **Critical problem for this project:** on a corpus of ~1500 items at moderate embedding scale (256-768d), UMAP + HDBSCAN with default parameters frequently labels 30-60% of points as outliers (noise class -1). These points must be reassigned post-hoc, negating the semantic purity advantage [3].
- Soft clustering (`hdbscan.all_points_membership_vectors()`) partially addresses noise by assigning probability membership rather than hard -1 labels [4]. BERTopic's `reduce_outliers()` wraps this.

### Agglomerative Clustering (Ward / Average linkage)
- Builds a complete dendrogram over all 1500 items. No outlier class — every point is assigned to exactly one cluster.
- Ward linkage minimizes intra-cluster variance at each merge step, producing compact spherical-ish clusters. **Note:** Ward requires Euclidean distance; for cosine-distance embeddings, L2-normalize vectors first (converting cosine distance to Euclidean-equivalent) [5].
- For knowledge-graph entity canonicalization specifically, the literature shows HAC (Hierarchical Agglomerative Clustering) is the most-used approach [6]. Recent work combining BiLSTM + HAC achieves good canonicalization on ambiguous short-label strings.
- A November 2025 paper comparing spectral methods against HAC, HDBSCAN, and OPTICS on six short-text datasets found that KMeans and HAC guided by a spectral cluster-count estimator "significantly outperform HDBSCAN, OPTICS, and Leiden" on short-text data [7]. This is the most directly relevant recent result.
- **Weakness:** Ward assumes similarly-sized clusters. If your topic distribution is Zipfian (a few mega-topics + many niche topics), Ward will split the mega-topics aggressively. Use average or complete linkage as an alternative.

### BERTopic
- BERTopic is a modular pipeline, not a distinct algorithm. Default backend: UMAP → HDBSCAN → c-TF-IDF topic labeling.
- The key value-add is c-TF-IDF topic *naming* (useful) and the ability to swap clustering backends. You can plug in `AgglomerativeClustering(n_clusters=300)` directly [8].
- Hierarchy in BERTopic is generated *post-hoc* from the c-TF-IDF matrix: topic vectors are clustered with scipy Ward to build a dendrogram of topics-of-topics [8].
- **For this project's use case** (1500 short labels, no full documents, no TF-IDF signal): BERTopic's c-TF-IDF step adds no value. You have labels, not document bags-of-words. Use BERTopic only if you want its visualization tooling (`visualize_hierarchy`); the clustering step itself should be replaced with agglomerative.

### GraphRAG Leiden
- Leiden is a *graph community detection* algorithm that optimizes modularity over an entity co-occurrence graph. It operates on edges (co-occurrence counts, co-mention relationships), not on raw embedding vectors [9].
- GraphRAG pipeline: LLM extracts entities → edges formed by co-occurrence → Leiden partitions entities into communities → recursive Leiden sub-partitions create hierarchy.
- **Wrong fit here.** You do not have a co-occurrence graph — you have 1472 isolated topic strings and their embeddings. Leiden has no edges to partition. Forcing it would require first building a k-NN graph from embeddings (which is what HDBSCAN does internally anyway), adding complexity without benefit.
- Leiden's `resolution` parameter (default 1.0) controls community granularity but is currently hardcoded in GraphRAG's `hierarchical_leiden` call [10]. Tuning it to produce exactly 200-400 communities is harder than just setting `n_clusters=300` in scikit-learn.

### OpenAI Cookbook Recipe
- Uses KMeans directly on full-dimensional embeddings (1536d for text-embedding-3-small), with `n_clusters` set explicitly [11].
- Workflow: load embeddings → `KMeans(n_clusters=k, init='k-means++')` → analyze cluster characteristics.
- **No dimensionality reduction, no hierarchy.** The cookbook is a demo, not a production recipe for hierarchical concept ontology building. KMeans assumes convex equal-sized clusters and provides no dendrogram.
- Reasonable baseline but inferior to agglomerative for this use case.

---

## (b) How to Enforce a Target Cluster Count (200-400) When the Algorithm Is Density-Based

The core tension: HDBSCAN determines cluster count from data density, not from user specification. Three strategies exist, each with trade-offs:

### Strategy 1: `min_cluster_size` binary search (HDBSCAN native)
- Decrease `min_cluster_size` → more clusters; increase → fewer clusters.
- For 1500 items targeting 200-400 clusters, start at `min_cluster_size=3` (minimum meaningful cluster), then tune upward.
- **Problem:** at `min_cluster_size=3-5` on 1500 items, you typically get either too many micro-clusters (500+) or 30-60% noise. The useful range is narrow and data-dependent.
- `cluster_selection_method='leaf'` produces more fine-grained clusters than the default `'eom'`; useful when you want 200+ clusters [12]. However, a 2025 empirical study found `eom` universally outperforms `leaf` on clustering quality metrics (AMI) [13].
- `cluster_selection_epsilon`: merges nearby clusters, reducing count. Set to ~0.05-0.15 of the typical pairwise cosine distance to eliminate micro-clusters without collapsing genuine ones.

### Strategy 2: BERTopic `reduce_topics(nr_topics=N)` post-hoc
- Run HDBSCAN at fine granularity (allowing 400-800 clusters), then merge using BERTopic's `reduce_topics()` which merges via c-TF-IDF similarity.
- This gives post-hoc count control but the merged clusters may not be as semantically tight as native agglomerative merges.

### Strategy 3: Agglomerative with explicit `n_clusters` (recommended)
- `AgglomerativeClustering(n_clusters=300, linkage='ward')` — direct, deterministic, no binary search needed [5].
- For cosine distance: L2-normalize embeddings first, then use Euclidean Ward. Alternatively, use `linkage='average', metric='cosine'` which accepts cosine natively.
- To scan the 200-400 range: fit once with `distance_threshold=0, compute_full_tree=True`, then use `scipy.cluster.hierarchy.fcluster(Z, t=k, criterion='maxclust')` to cut at any k without refitting [5].

---

## (c) Which Gives a Hierarchy "For Free"

| Algorithm | Native hierarchy? | How |
|---|---|---|
| Agglomerative (Ward) | **Yes — full dendrogram** | `children_` attribute encodes every merge. One fit, cut at any level: `n_clusters=300` for L0 concepts, `n_clusters=50` for L0 super-concepts. |
| HDBSCAN | **Yes — condensed cluster tree** | `hdbscan_.condensed_tree_` / `single_linkage_tree_` gives the full hierarchy. Practical extraction is complex; `hdbscan.plots.CondensedTree().plot()` visualizes it. |
| BERTopic | **Yes — post-hoc topic dendrogram** | `hierarchical_topics()` applies scipy Ward to c-TF-IDF topic vectors. This gives topics-of-topics, not the raw point hierarchy. |
| KMeans | No | No hierarchical structure. Would need separate agglomerative step on cluster centroids. |
| GraphRAG Leiden | **Yes — recursive** | Each community is recursively sub-partitioned. But only if you have edge-based input, not raw embeddings. |

**Practical recommendation for this project:**

Fit one full agglomerative tree on all 1500 embeddings:
```python
from sklearn.cluster import AgglomerativeClustering
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage as scipy_linkage

# L2-normalize for cosine-equivalent Ward
X_norm = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

model = AgglomerativeClustering(
    n_clusters=None,
    distance_threshold=0,
    compute_full_tree=True,
    compute_distances=True,
    linkage='ward'
)
model.fit(X_norm)

# Now extract at two levels:
# Level 1 — L0 Concepts (200-350 clusters)
from sklearn.cluster import AgglomerativeClustering as AC
labels_l0 = AC(n_clusters=300, linkage='ward').fit_predict(X_norm)

# Level 0 — super-concepts (40-60 clusters, for nav/grouping in viewer)
labels_super = AC(n_clusters=50, linkage='ward').fit_predict(X_norm)
```

The `children_` array encodes parent-child: `children_[i]` gives the two sub-nodes merged at step i. This maps directly to the `parent_id` field in `knowledge/concepts.json`.

---

## (d) Practical Advice on Threshold Tuning

### If using Agglomerative (Ward) — recommended
1. **Baseline run:** fit with `n_clusters=300`. Spot-check 20-30 clusters visually (list member strings). If clusters look split too finely, increase; if merged too aggressively, decrease.
2. **Scan range:** build the full tree once (O(n²)), then use `fcluster` to test every integer k from 150 to 450 in one pass without refitting. Plot silhouette scores across k; pick the inflection point.
3. **Cosine vs Euclidean:** Ward internally uses Euclidean. L2-normalize embeddings before fitting so Euclidean distance approximates cosine distance. Alternatively test `linkage='average', metric='cosine'` — average linkage with cosine often produces more semantically intuitive clusters for NLP embeddings [6].
4. **Noisy short labels:** Ward will forcibly assign every label to a cluster. For the ~50-100 extremely specific single-word or jargon labels, they will end up in the nearest cluster. This is acceptable — the Phase 1a Haiku verification step will `kick` them if they don't belong.

### If using HDBSCAN (fallback if Ward quality disappoints)
1. **Start:** `min_cluster_size=3, min_samples=1` on UMAP-reduced embeddings (n_components=10-20, n_neighbors=10-15 for 1500 items).
2. **Tune loop:** grid-search over `min_cluster_size=[2,3,4,5]` × `cluster_selection_method=['eom','leaf']` × `cluster_selection_epsilon=[0.0, 0.05, 0.1]`. Log cluster count and noise percentage for each. Select the combination giving cluster count in [200,400] and noise < 20%.
3. **Noise reassignment:** always run soft clustering (`all_points_membership_vectors`) to reassign noise points. Or use BERTopic's `reduce_outliers(strategy='embeddings')` which assigns each noise point to its nearest cluster centroid [8].
4. **Practical parameters seen in literature** for 1500 short texts: `n_neighbors=6, n_components=9, min_cluster_size=6` (TDS article, best result on intent clustering [3]).

### UMAP settings (relevant for HDBSCAN path)
- `n_neighbors=10-15` for 1500 items. Too small (< 5) creates isolated islands; too large (> 30) over-smooths.
- `n_components=10-20` is standard for downstream clustering. Not needed for agglomerative (which operates on full-d embeddings or cosine pairwise distance).
- `metric='cosine'` in UMAP for text embeddings.

### Validation metric without ground truth
- **Silhouette score** on a random 200-item sample (full computation on 1500×1500 is feasible).
- **Cohesion Ratio** (proposed in Nov 2025 arxiv [7]): quantifies how much intra-cluster similarity exceeds the global similarity background. Correlates strongly with NMI without needing labels.
- **Manual spot-check:** random sample of 20 clusters, list all members. The Haiku verification phase (Phase 1a) is essentially a structured manual spot-check.

---

## Algorithm Selection Matrix

| Criterion | HDBSCAN | Ward Agglomerative | BERTopic | Leiden (GraphRAG) | KMeans |
|---|---|---|---|---|---|
| Semantic cluster purity (short labels) | High (with UMAP) | High | High (uses HDBSCAN) | N/A (wrong input) | Medium |
| Enforce target count (200-400) | Hard | Trivial (`n_clusters=300`) | Medium (reduce_topics) | Hard | Trivial |
| Hierarchy for free | Yes (complex) | **Yes (simple dendrogram)** | Yes (c-TF-IDF ward) | Yes (recursive) | No |
| No orphan/noise points | No (30-60% noise risk) | **Yes (all assigned)** | No (HDBSCAN noise) | N/A | Yes |
| Complexity at n=1500 | O(n log n) | O(n²) → fine at 1500 | O(n log n) | N/A | O(nk·iter) |
| Deterministic | No | **Yes** | No | No | No |
| Requires graph input | No | No | No | **Yes** | No |

**Winner for this project: Agglomerative Ward on L2-normalized embeddings, `n_clusters=300`, with a second cut at `n_clusters=50` for super-concept grouping.**

---

## Recommended Implementation Plan for Phase 1a

```python
import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import normalize

# 1. Load 1472 topic embeddings (shape: [1472, embedding_dim])
# embeddings = load_embeddings()  # from Phase 0a #04 recommended model

# 2. L2-normalize for cosine-equivalent Ward
X = normalize(embeddings)

# 3. Fit full tree once
from sklearn.cluster import AgglomerativeClustering
model = AgglomerativeClustering(
    n_clusters=300,      # Start here; tune based on spot-check
    linkage='ward',
    compute_distances=True
)
labels = model.fit_predict(X)

# 4. For super-concept grouping (L0 parent of L0):
super_labels = AgglomerativeClustering(n_clusters=50, linkage='ward').fit_predict(X)

# 5. Emit clusters:
# {cluster_id, members: [topic_strings], proposed_canonical: <centroid-nearest>,
#  super_cluster_id: super_labels[centroid_index]}
```

If after Phase 1a Haiku verification, >15% of clusters get `split` or `merge_with` verdicts, re-run with `n_clusters=350` (split-heavy) or `n_clusters=250` (merge-heavy) and re-verify a 20-cluster sample before running all 15 agents.

---

## Sources

1. [Improving the Performance of HDBSCAN on Short Text Clustering by Using Word Embedding and UMAP (IEEE)](https://ieeexplore.ieee.org/document/9640285/)
2. [ResearchGate: HDBSCAN + UMAP short text clustering](https://www.researchgate.net/publication/357109700_Improving_the_Performance_of_HDBSCAN_on_Short_Text_Clustering_by_Using_Word_Embedding_and_UMAP)
3. [Clustering sentence embeddings to identify intents in short text (Towards Data Science)](https://towardsdatascience.com/clustering-sentence-embeddings-to-identify-intents-in-short-text-48d22d3bf02e/)
4. [How Soft Clustering for HDBSCAN Works — hdbscan 0.8.1 docs](https://hdbscan.readthedocs.io/en/latest/soft_clustering_explanation.html)
5. [AgglomerativeClustering — scikit-learn 1.9.0 documentation](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.AgglomerativeClustering.html)
6. [Relation Canonicalization in Open Knowledge Graphs (ESWC 2022)](https://2022.eswc-conferences.org/wp-content/uploads/2022/05/pd_Lomaeva_et_al_paper_240.pdf)
7. [Scalable Parameter-Light Spectral Method for Clustering Short Text Embeddings (arxiv Nov 2025)](https://arxiv.org/html/2511.19350)
8. [BERTopic Hierarchical Topic Modeling docs](https://maartengr.github.io/BERTopic/getting_started/hierarchicaltopics/hierarchicaltopics.html)
9. [From Local to Global: A GraphRAG Approach (arxiv 2404.16130)](https://arxiv.org/html/2404.16130v2)
10. [GraphRAG Community Detection — Microsoft/Mintlify](https://www.mintlify.com/microsoft/graphrag/concepts/community-detection)
11. [OpenAI Cookbook Clustering.ipynb (GitHub)](https://github.com/openai/openai-cookbook/blob/main/examples/Clustering.ipynb)
12. [Parameter Selection for HDBSCAN — hdbscan 0.8.1 docs](https://hdbscan.readthedocs.io/en/latest/parameter_selection.html)
13. [Tuning with HDBSCAN (Towards Data Science)](https://towardsdatascience.com/tuning-with-hdbscan-149865ac2970/)
14. [2.3 Clustering — scikit-learn 1.9.0 documentation](https://scikit-learn.org/stable/modules/clustering.html)
15. [BERTopic Best Practices](https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html)
16. [BERTopic Clustering backends](https://maartengr.github.io/BERTopic/getting_started/clustering/clustering.html)
17. [Human-interpretable clustering of short text using LLMs (PMC/NIH)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11750404/)
