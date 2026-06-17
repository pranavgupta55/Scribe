#!/usr/bin/env python3
"""Phase 3b — mechanical merge + batch for Haiku classification.

1. Load claims_pool.jsonl (2245 claims) + auto_merge pairs (sim ≥ 0.85).
2. Union-find to collapse the auto_merge pairs into merged_claims.
3. For each merged cluster, pick the centroid-nearest claim as canonical text;
   union conditions[]/bounded_by[]; build attribution_list with one entry per
   member claim's (source_file, speaker, speaker_term).
4. Split the merged_claims into 12 batches for Haiku classification agents.

Outputs:
  .scribe-skills/phase3b/merged_claims.jsonl  (one merged claim per line)
  .scribe-skills/phase3b/classify_batches/batch_{01..12}.json
"""
import json, sys
from pathlib import Path
import numpy as np

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P3B = ROOT / ".scribe-skills/phase3b"
OUT_BATCHES = P3B / "classify_batches"
OUT_BATCHES.mkdir(parents=True, exist_ok=True)

N_BATCHES = 12


def union_find_collapse(claims, auto_merge_pairs):
    """Run union-find on the auto_merge pairs; return cluster_id -> list of claim_ids."""
    parent = list(range(len(claims)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry: parent[rx] = ry
    for p in auto_merge_pairs:
        union(p["a_id"], p["b_id"])
    clusters = {}
    for i in range(len(claims)):
        r = find(i)
        clusters.setdefault(r, []).append(i)
    return clusters


def pick_centroid(member_ids, A):
    if len(member_ids) == 1:
        return member_ids[0]
    sub = A[member_ids]
    c = sub.mean(axis=0)
    c /= np.linalg.norm(c) + 1e-9
    sims = sub @ c
    return member_ids[int(np.argmax(sims))]


def build_merged_claim(member_ids, claims, A):
    """Pick the centroid as canonical text; union the conditional fields."""
    cent = pick_centroid(member_ids, A)
    base = claims[cent]
    # Union conditions / bounded_by across all members
    conditions, bounds = set(), set()
    for mid in member_ids:
        for c in (claims[mid].get("conditions") or []):
            if c and c.strip(): conditions.add(c.strip())
        for b in (claims[mid].get("bounded_by") or []):
            if b and b.strip(): bounds.add(b.strip())
    # Attribution list = one entry per member
    attribution_list = []
    for mid in member_ids:
        c = claims[mid]
        attribution_list.append({
            "source_file": c["source_file"],
            "speaker": c["speaker"],
            "speaker_term": c.get("speaker_term"),
            "type": c.get("type"),
            "mechanism": c.get("mechanism"),
            "numbers": c.get("numbers"),
        })
    return {
        "claim_id": cent,                 # use centroid claim_id as merged id
        "topic": base["topic"],
        "text": base["text"],
        "type": base.get("type", "assertion"),
        "mechanism": base.get("mechanism"),
        "numbers": base.get("numbers"),
        "conditions": sorted(conditions),
        "bounded_by": sorted(bounds),
        "primary_speaker": base["primary_speaker"],
        "speaker_term": base.get("speaker_term"),
        "n_sources": len(set(claims[mid]["source_file"] for mid in member_ids)),
        "n_members": len(member_ids),
        "member_claim_ids": member_ids,
        "attribution_list": attribution_list,
    }


def main():
    # Load claims
    claims = []
    with (P3B / "claims_pool.jsonl").open() as f:
        for line in f:
            claims.append(json.loads(line))
    print(f"claims: {len(claims)}", file=sys.stderr)

    # Load embeddings + pairs
    A = np.load(P3B / "embeddings.npy")
    A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
    pairs = json.loads((P3B / "candidate_pairs.json").read_text())
    auto = pairs["auto_merge"]
    print(f"auto_merge pairs: {len(auto)}", file=sys.stderr)

    # Union-find
    clusters = union_find_collapse(claims, auto)
    print(f"clusters: {len(clusters)}  (collapsed from {len(claims)})", file=sys.stderr)

    # Build merged_claims
    merged = []
    for cid, member_ids in clusters.items():
        merged.append(build_merged_claim(sorted(member_ids), claims, A))
    merged.sort(key=lambda c: -c["n_sources"])

    # Stats
    multi_source = [c for c in merged if c["n_sources"] >= 2]
    print(f"multi-source claims (≥2 sources): {len(multi_source)}", file=sys.stderr)
    top_multi = sorted(merged, key=lambda c: -c["n_sources"])[:5]
    print("top by n_sources:", file=sys.stderr)
    for c in top_multi:
        print(f"  {c['n_sources']} sources · {c['topic'][:30]:30s} · {c['text'][:80]}", file=sys.stderr)

    # Write merged claims
    mp = P3B / "merged_claims.jsonl"
    with mp.open("w") as f:
        for c in merged:
            f.write(json.dumps(c) + "\n")
    print(f"wrote {mp.relative_to(ROOT)}  ({len(merged)} merged claims)", file=sys.stderr)

    # Split into N_BATCHES for Haiku classification
    per = (len(merged) + N_BATCHES - 1) // N_BATCHES
    for i in range(N_BATCHES):
        chunk = merged[i*per:(i+1)*per]
        if not chunk: break
        bp = OUT_BATCHES / f"batch_{i+1:02d}.json"
        # Slim down for the classification prompt — drop attribution_list (too verbose)
        slim = [{
            "claim_id": c["claim_id"],
            "topic": c["topic"],
            "text": c["text"],
            "type": c["type"],
            "conditions": c["conditions"],
            "mechanism": c["mechanism"],
            "numbers": c["numbers"],
            "bounded_by": c["bounded_by"],
            "n_sources": c["n_sources"],
        } for c in chunk]
        bp.write_text(json.dumps({"batch_id": i+1, "claims": slim}, indent=1))
        print(f"  wrote {bp.relative_to(ROOT)}  ({len(chunk)} claims)", file=sys.stderr)


if __name__ == "__main__":
    main()
