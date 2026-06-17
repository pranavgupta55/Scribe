#!/usr/bin/env python3
"""Phase 1a mechanical pre-pass: embed 1472 topic strings via qwen3-embedding:8b,
L2-normalize, run Agglomerative Ward at n_clusters=300 (L0 concepts) and
n_clusters=50 (super-concepts). Emit cluster batches for the verification agents.

Inputs:
  - knowledge/sources.json    : topics[] per source + video_summary
  - knowledge/topics/*.md     : per-topic headline (the `> ...` line)
Outputs:
  - .scribe-skills/phase1a/embeddings.npy
  - .scribe-skills/phase1a/clusters.json  (full state — for re-runs)
  - .scribe-skills/phase1a/verify_batches/batch_NN.json (20 files, ~15 clusters each)
  - .scribe-skills/phase1a/hard_clusters.json (the 10-15 highest-variance clusters for Sonnet judges)

Per 0a-research-06: embed `f"{topic_string}: {topic_headline}"` to enrich short labels with their
extracted context line. Qwen3-embedding-8b is instruction-tuned; per 0a-research-04 we use the
Qwen3 task prefix for retrieval-style clustering.
"""
import json
import re
import sys
import time
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
OUT_DIR = ROOT / ".scribe-skills/phase1a"
BATCH_DIR = OUT_DIR / "verify_batches"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BATCH_DIR.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = "qwen3-embedding:8b"
N_CLUSTERS_L0 = 300       # target L0 Concept count
N_CLUSTERS_SUPER = 50     # super-concept grouping
N_BATCHES = 20            # ~15 clusters per Haiku verify agent
N_HARD = 12               # hardest clusters routed to Sonnet judges

# Qwen3-Embedding instruction prefix per 0a-04 §7. Document side gets no prefix.
QWEN3_QUERY_INSTRUCTION = "Instruct: Retrieve semantically similar text.\nQuery: "


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def load_topic_universe():
    """Returns: list of {topic, sources, headline, summary, embed_text} dicts, in canonical order."""
    src = json.loads((ROOT / "knowledge/sources.json").read_text())
    topic_sources = defaultdict(list)
    for sname, meta in src.items():
        for t in meta.get("topics", []):
            topic_sources[t].append(sname)

    # Per-topic headline from knowledge/topics/<slug>.md
    headlines = {}
    summaries = {}
    topics_dir = ROOT / "knowledge/topics"
    for t in topic_sources:
        p = topics_dir / f"{slug(t)}.md"
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                m = re.search(r"^>\s*(.+)$", text, re.M)
                if m:
                    headlines[t] = m.group(1).strip()
            except Exception:
                pass

    out = []
    for t, srcs in sorted(topic_sources.items()):
        head = headlines.get(t, "")
        # Per 0a-06: embed body text in the form
        #   "{topic_label}: {headline}"
        # which mirrors the title+premise prepend pattern that 0a-06 recommends.
        # The topic label IS the title; the headline IS the premise/summary.
        body = f"{t}: {head}" if head else t
        out.append({
            "topic": t,
            "n_sources": len(srcs),
            "sources": srcs,
            "headline": head,
            "embed_text": body,
        })
    return out


def embed_all(topics):
    """Embed every topic's `embed_text` via ollama. Returns numpy array shape (N, D)."""
    import ollama
    vectors = []
    t0 = time.time()
    for i, t in enumerate(topics):
        # Apply Qwen3 retrieval-style instruction prefix to the QUERY side; for clustering
        # we treat each topic identically so use the same prefix on every item.
        text = QWEN3_QUERY_INSTRUCTION + t["embed_text"]
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text)
        v = resp.get("embedding") or resp.get("embeddings")
        vectors.append(v)
        if (i + 1) % 100 == 0 or i + 1 == len(topics):
            elapsed = time.time() - t0
            rate = (i + 1) / max(1e-3, elapsed)
            eta = (len(topics) - i - 1) / max(1e-3, rate)
            print(f"  embed {i+1}/{len(topics)}  ({rate:.1f}/s, ETA {eta:.0f}s)", file=sys.stderr)
    A = np.asarray(vectors, dtype=np.float32)
    # L2-normalize for cosine-equivalent Ward
    A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
    return A


def cluster_ward(A, n_clusters):
    from sklearn.cluster import AgglomerativeClustering
    model = AgglomerativeClustering(
        n_clusters=n_clusters, linkage="ward", compute_distances=True
    )
    return model.fit_predict(A), model


def centroid_nearest_member(member_indices, A):
    """Pick the member with embedding closest to the cluster centroid (cosine)."""
    centroid = A[member_indices].mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-9
    sims = A[member_indices] @ centroid
    return member_indices[int(np.argmax(sims))]


def main():
    print("loading topic universe...", file=sys.stderr)
    topics = load_topic_universe()
    print(f"  {len(topics)} unique topic strings", file=sys.stderr)

    emb_path = OUT_DIR / "embeddings.npy"
    if emb_path.exists() and "--force-embed" not in sys.argv:
        print(f"  loading cached embeddings from {emb_path.name}", file=sys.stderr)
        A = np.load(emb_path)
        if A.shape[0] != len(topics):
            print(f"  cached shape mismatch ({A.shape[0]} vs {len(topics)}), re-embedding", file=sys.stderr)
            A = embed_all(topics)
            np.save(emb_path, A)
    else:
        print(f"embedding via {EMBED_MODEL}...", file=sys.stderr)
        A = embed_all(topics)
        np.save(emb_path, A)
    print(f"  embeddings shape={A.shape}", file=sys.stderr)

    # Sanity check #1 — Lead Generation family closeness
    lead_idxs = [i for i, t in enumerate(topics) if "lead" in t["topic"].lower()]
    if len(lead_idxs) >= 2:
        L = A[lead_idxs]
        sim = L @ L.T
        np.fill_diagonal(sim, np.nan)
        avg = np.nanmean(sim)
        print(f"  sanity: {len(lead_idxs)} 'lead*' topics, mean intra-family cos sim = {avg:.3f}", file=sys.stderr)
    else:
        print(f"  sanity: only {len(lead_idxs)} lead-family topics, skipping", file=sys.stderr)

    # Cluster
    print(f"\nclustering Ward n_clusters={N_CLUSTERS_L0}...", file=sys.stderr)
    labels_l0, _ = cluster_ward(A, N_CLUSTERS_L0)
    print(f"clustering Ward n_clusters={N_CLUSTERS_SUPER}...", file=sys.stderr)
    labels_super, _ = cluster_ward(A, N_CLUSTERS_SUPER)

    # Build cluster records
    clusters = []
    by_l0 = defaultdict(list)
    for i, lbl in enumerate(labels_l0):
        by_l0[int(lbl)].append(i)

    # Pre-compute super-cluster id per L0 cluster (majority vote of members)
    super_for_l0 = {}
    for lbl, idxs in by_l0.items():
        super_for_l0[lbl] = Counter(int(labels_super[i]) for i in idxs).most_common(1)[0][0]

    for lbl, idxs in sorted(by_l0.items()):
        idxs = list(idxs)
        centroid_idx = centroid_nearest_member(idxs, A)
        # Variance metric for hardness ranking — high variance = ambiguous cluster.
        sub = A[idxs]
        c = sub.mean(axis=0)
        c /= np.linalg.norm(c) + 1e-9
        sims_to_c = sub @ c
        variance = float(1 - sims_to_c.mean())   # higher = looser cluster
        min_sim = float(sims_to_c.min())
        clusters.append({
            "cluster_id": int(lbl),
            "super_cluster_id": int(super_for_l0[lbl]),
            "size": len(idxs),
            "proposed_canonical": topics[centroid_idx]["topic"],
            "members": [
                {
                    "topic": topics[i]["topic"],
                    "headline": topics[i]["headline"][:200],
                    "n_sources": topics[i]["n_sources"],
                    "is_centroid": (i == centroid_idx),
                }
                for i in idxs
            ],
            "variance": variance,
            "min_intra_sim": min_sim,
        })

    # Sort by variance descending; top N_HARD go to Sonnet judges
    by_variance = sorted(clusters, key=lambda c: -c["variance"])
    hard_ids = {c["cluster_id"] for c in by_variance[:N_HARD]}

    haiku_clusters = [c for c in clusters if c["cluster_id"] not in hard_ids]
    sonnet_clusters = [c for c in clusters if c["cluster_id"] in hard_ids]
    print(f"\nrouting: {len(haiku_clusters)} clusters → Haiku, {len(sonnet_clusters)} → Sonnet", file=sys.stderr)

    # Write the full clusters.json
    (OUT_DIR / "clusters.json").write_text(json.dumps({
        "embed_model": EMBED_MODEL,
        "n_clusters_l0": N_CLUSTERS_L0,
        "n_clusters_super": N_CLUSTERS_SUPER,
        "n_topics": len(topics),
        "clusters": clusters,
    }, indent=2))

    # Write hard clusters for Sonnet
    (OUT_DIR / "hard_clusters.json").write_text(json.dumps({
        "clusters": sonnet_clusters,
    }, indent=2))
    print(f"  wrote {OUT_DIR.relative_to(ROOT)}/clusters.json + hard_clusters.json", file=sys.stderr)

    # Split Haiku clusters into N_BATCHES batches of ~equal size
    haiku_clusters_sorted = sorted(haiku_clusters, key=lambda c: c["cluster_id"])
    per_batch = (len(haiku_clusters_sorted) + N_BATCHES - 1) // N_BATCHES
    for i in range(N_BATCHES):
        batch = haiku_clusters_sorted[i * per_batch : (i + 1) * per_batch]
        if not batch:
            continue
        p = BATCH_DIR / f"batch_{i+1:02d}.json"
        p.write_text(json.dumps({"batch_id": i + 1, "clusters": batch}, indent=2))
        print(f"  wrote {p.relative_to(ROOT)}  ({len(batch)} clusters)", file=sys.stderr)

    # Summary stats
    sizes = [c["size"] for c in clusters]
    print(f"\nfinal stats:", file=sys.stderr)
    print(f"  cluster sizes: min={min(sizes)} median={sorted(sizes)[len(sizes)//2]} "
          f"mean={sum(sizes)/len(sizes):.1f} max={max(sizes)}", file=sys.stderr)
    singletons = sum(1 for s in sizes if s == 1)
    print(f"  singletons: {singletons} ({100*singletons/len(sizes):.1f}%)", file=sys.stderr)
    print(f"  variance percentiles (lower is tighter): "
          f"p50={sorted(c['variance'] for c in clusters)[len(clusters)//2]:.3f}  "
          f"p90={sorted(c['variance'] for c in clusters)[int(0.9*len(clusters))]:.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
