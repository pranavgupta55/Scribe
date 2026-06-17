#!/usr/bin/env python3
"""Phase 4 — compute candidate edges for the connection rebuild.

Two pools of candidate edges:
  1. Concept ↔ Concept (L0↔L0)
     For each canonical L0 concept, find top-K=12 semantic neighbors via embedding
     similarity, threshold sim ≥ 0.45.  Already-cached embeddings in
     .scribe-skills/phase1a/embeddings.npy aren't keyed by concept_id (they're keyed
     by the original 1472 topic strings), so we rebuild concept embeddings here from
     the concepts.json canonical_name + definition.

  2. Claim ↔ Claim, cross-concept (L2↔L2 / L2a / L2b)
     For each claim, find top-K=8 nearest claims on a DIFFERENT canonical topic.
     Threshold sim ≥ 0.45.  Demote same-source pairs.

Output:
  .scribe-skills/phase4/candidates.json    — {concept_pairs[], claim_pairs[]}
  .scribe-skills/phase4/batches/batch_NN.json — 40 batches for Haiku judgment
"""
import json, sys, time
from pathlib import Path
from collections import defaultdict
import numpy as np

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P3B = ROOT / ".scribe-skills/phase3b"
P4 = ROOT / ".scribe-skills/phase4"
P4_BATCHES = P4 / "batches"
P4_BATCHES.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = "qwen3-embedding:8b"
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "
THRESH = 0.45
K_CONCEPT = 12
K_CLAIM = 8
N_BATCHES = 40


def embed_texts(texts):
    import ollama
    print(f"embedding {len(texts)} texts...", file=sys.stderr)
    out, t0 = [], time.time()
    for i, t in enumerate(texts):
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=QWEN3_PREFIX + (t or ""))
        out.append(resp.get("embedding") or resp.get("embeddings"))
        if (i+1) % 100 == 0 or i+1 == len(texts):
            elapsed = time.time() - t0
            rate = (i+1) / max(1e-3, elapsed)
            eta = (len(texts)-i-1) / max(1e-3, rate)
            print(f"  {i+1}/{len(texts)}  ({rate:.1f}/s, ETA {eta:.0f}s)", file=sys.stderr)
    A = np.asarray(out, dtype=np.float32)
    A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
    return A


def main():
    # Load L0 concepts
    cdata = json.loads((ROOT / "knowledge/concepts.json").read_text())
    concepts = cdata["concepts"]
    print(f"L0 concepts: {len(concepts)}", file=sys.stderr)

    # Embed concepts: name + definition
    c_emb_path = P4 / "concept_embeddings.npy"
    if c_emb_path.exists() and "--force-embed" not in sys.argv:
        Ac = np.load(c_emb_path)
    else:
        texts = [f"{c['canonical_name']}: {c['definition']}" for c in concepts]
        Ac = embed_texts(texts)
        np.save(c_emb_path, Ac)
    print(f"concept embeddings: {Ac.shape}", file=sys.stderr)

    # L0↔L0 candidate pairs
    sim_c = Ac @ Ac.T
    np.fill_diagonal(sim_c, -1)
    concept_pairs = []
    seen_c = set()
    for i in range(len(concepts)):
        top = np.argsort(-sim_c[i])[:K_CONCEPT]
        for j in top:
            j = int(j)
            if i == j: continue
            s = float(sim_c[i, j])
            if s < THRESH: continue
            a, b = (i, j) if i < j else (j, i)
            key = (a, b)
            if key in seen_c: continue
            seen_c.add(key)
            concept_pairs.append({
                "a_concept_id": a, "b_concept_id": b,
                "a_name": concepts[a]["canonical_name"],
                "b_name": concepts[b]["canonical_name"],
                "a_def": concepts[a]["definition"],
                "b_def": concepts[b]["definition"],
                "sim": round(s, 3),
            })
    print(f"concept↔concept candidate pairs: {len(concept_pairs)}", file=sys.stderr)

    # Load L2/L2a/L2b claim nodes (skip examples/practices/frameworks for connection judgment)
    claims = []
    with (ROOT / "knowledge/v2/nodes.jsonl").open() as f:
        for line in f:
            n = json.loads(line)
            if n.get("node_id", "").startswith("claim:") and n.get("level", "").startswith("L2"):
                claims.append(n)
    print(f"claim nodes (L2/L2a/L2b): {len(claims)}", file=sys.stderr)

    # Embed claims (use the cached phase3b embeddings if claim_id ranges line up;
    # otherwise re-embed).  Simpler: re-embed from text since merged_claims used
    # centroid text which may differ from cached vectors at the same id slot.
    cl_emb_path = P4 / "claim_embeddings.npy"
    if cl_emb_path.exists() and "--force-embed" not in sys.argv:
        Acl = np.load(cl_emb_path)
        if Acl.shape[0] != len(claims):
            print(f"  stale claim embeddings ({Acl.shape[0]} vs {len(claims)}); re-embedding", file=sys.stderr)
            Acl = embed_texts([c["text"] for c in claims])
            np.save(cl_emb_path, Acl)
    else:
        Acl = embed_texts([c["text"] for c in claims])
        np.save(cl_emb_path, Acl)
    print(f"claim embeddings: {Acl.shape}", file=sys.stderr)

    # Claim↔Claim cross-concept candidate pairs
    sim_cl = Acl @ Acl.T
    np.fill_diagonal(sim_cl, -1)
    claim_pairs = []
    seen_cl = set()
    for i in range(len(claims)):
        # mask out same-topic claims
        topic_i = claims[i]["topic"]
        order = np.argsort(-sim_cl[i])
        picked = 0
        for j in order:
            j = int(j)
            if picked >= K_CLAIM: break
            if i == j: continue
            s = float(sim_cl[i, j])
            if s < THRESH: break  # sorted descending
            topic_j = claims[j]["topic"]
            if topic_j == topic_i: continue  # same concept → skip
            a, b = (i, j) if i < j else (j, i)
            key = (a, b)
            if key in seen_cl: continue
            seen_cl.add(key)
            # Demote same-source pairs (intra-source connections often spurious)
            same_source = any(
                att_a.get("source_file") == att_b.get("source_file")
                for att_a in claims[a].get("attribution_list", [])
                for att_b in claims[b].get("attribution_list", [])
            )
            if same_source: continue
            claim_pairs.append({
                "a_id": a, "b_id": b,
                "a_topic": claims[a]["topic"], "b_topic": claims[b]["topic"],
                "a_text": claims[a]["text"][:400],
                "b_text": claims[b]["text"][:400],
                "a_speaker": claims[a].get("primary_speaker"),
                "b_speaker": claims[b].get("primary_speaker"),
                "a_level": claims[a].get("level"),
                "b_level": claims[b].get("level"),
                "sim": round(s, 3),
            })
            picked += 1
    print(f"claim↔claim cross-concept candidate pairs: {len(claim_pairs)}", file=sys.stderr)

    # Save the full candidates pool
    (P4 / "candidates.json").write_text(json.dumps({
        "concept_pairs": concept_pairs,
        "claim_pairs": claim_pairs,
    }, indent=1))
    print(f"\nwrote {(P4/'candidates.json').relative_to(ROOT)}", file=sys.stderr)

    # Batch for Haiku judgment.  Mix concept and claim pairs in each batch so
    # each agent sees both types and shared prompt structure.
    all_pairs = (
        [{**p, "kind": "concept"} for p in concept_pairs] +
        [{**p, "kind": "claim"} for p in claim_pairs]
    )
    print(f"total candidate pairs: {len(all_pairs)}", file=sys.stderr)

    per = (len(all_pairs) + N_BATCHES - 1) // N_BATCHES
    for i in range(N_BATCHES):
        chunk = all_pairs[i*per:(i+1)*per]
        if not chunk: break
        bp = P4_BATCHES / f"batch_{i+1:02d}.json"
        bp.write_text(json.dumps({"batch_id": i+1, "pairs": chunk}, indent=1))
        print(f"  wrote {bp.name}  ({len(chunk)} pairs)", file=sys.stderr)


if __name__ == "__main__":
    main()
