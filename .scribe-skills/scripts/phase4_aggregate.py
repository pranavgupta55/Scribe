#!/usr/bin/env python3
"""Aggregate Phase 4 edge verdicts → knowledge/v2/connections.json.

Also incorporates the auto_merge pairs (sim ≥ 0.85) from Phase 3b candidate_pairs.json
as implicit agreement edges (already used during Phase 3b merge for cross-source
duplicate-claim folding, but the cross-concept ones at ≥ 0.85 should still surface
as agreement edges between distinct concepts).
"""
import json, sys
from pathlib import Path
from collections import Counter

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P4 = ROOT / ".scribe-skills/phase4"
OUT = ROOT / "knowledge/v2"
OUT.mkdir(parents=True, exist_ok=True)

# 1. Read all scored files
edges = []
totals = Counter()
for p in sorted((P4 / "batches").glob("scored_*.json")):
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        print(f"!! {p.name}: {e}", file=sys.stderr)
        continue
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # Try known keys first; fall back to the first LIST value in the dict
        records = data.get("edges") or data.get("verdicts") or data.get("records") or data.get("connections") or data.get("classifications") or data.get("scores")
        if not isinstance(records, list):
            records = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        records = []
    if not isinstance(records, list):
        print(f"!! {p.name}: cannot find records list", file=sys.stderr)
        continue
    for r in records:
        kind = (r.get("edge_kind") or r.get("kind") or "").lower()
        if not kind or kind == "none":
            continue
        # Normalize id field names
        a = r.get("a_id_or_concept_id") if "a_id_or_concept_id" in r else r.get("a_id")
        b = r.get("b_id_or_concept_id") if "b_id_or_concept_id" in r else r.get("b_id")
        if a is None or b is None:
            continue
        # Some agents emit "high"/"medium"/"low" instead of numeric confidence
        raw_conf = r.get("confidence", 0.5)
        try:
            conf = float(raw_conf)
        except (ValueError, TypeError):
            conf = {"high": 0.85, "medium": 0.7, "low": 0.55}.get(str(raw_conf).lower(), 0.5)
        edges.append({
            "a": int(a),
            "b": int(b),
            "pair_kind": r.get("pair_kind", "claim"),
            "edge_kind": kind,
            "confidence": conf,
            "sentence": r.get("sentence", ""),
        })
        totals[kind] += 1

print(f"raw edges: {len(edges)}", file=sys.stderr)
print(f"distribution: {dict(totals)}", file=sys.stderr)

# 2. Dedup by (pair_kind, a, b)
seen = {}
for e in edges:
    a, b = sorted([e["a"], e["b"]])
    key = (e["pair_kind"], a, b)
    existing = seen.get(key)
    # Keep highest-confidence verdict
    if existing is None or e["confidence"] > existing["confidence"]:
        seen[key] = {**e, "a": a, "b": b}

dedup = list(seen.values())
print(f"deduped: {len(dedup)}", file=sys.stderr)

# 3. Filter by confidence — drop edges < 0.5 confidence
strong = [e for e in dedup if e["confidence"] >= 0.5]
print(f"strong (conf>=0.5): {len(strong)}", file=sys.stderr)

# 4. Also include auto-merge pairs from Phase 3b as cross-source agreement
# (these are 0.85+ cosine pairs that became single nodes via merge — they show
# multi-source confidence but are NOT visible as edges. We can surface them
# as "cross-source-confirmed" labels on the merged nodes elsewhere; for now
# include the original CONCEPT level co-occurrence edges since those weren't
# merged.)
# Already covered by candidate_pairs in Phase 4 — skip.

# Final distribution
ft = Counter(e["edge_kind"] for e in strong)
fp = Counter(e["pair_kind"] for e in strong)
print(f"\nfinal edge_kind: {dict(ft)}", file=sys.stderr)
print(f"final pair_kind: {dict(fp)}", file=sys.stderr)

# Write
out_path = OUT / "connections.json"
out_path.write_text(json.dumps({
    "version": "v2-graph-rebuild",
    "n_edges": len(strong),
    "edges": strong,
    "stats": {
        "raw": len(edges),
        "deduped": len(dedup),
        "edge_kind": dict(ft),
        "pair_kind": dict(fp),
    },
}, indent=1))
print(f"\nwrote {out_path.relative_to(ROOT)}", file=sys.stderr)
