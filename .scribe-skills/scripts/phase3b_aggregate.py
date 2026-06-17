#!/usr/bin/env python3
"""Aggregate Phase 3b classifications → knowledge/v2/nodes.jsonl.

Combines: merged_claims.jsonl (1413 claims with attribution_list) +
classify_batches/scored_NN.json (level per claim_id) →
each surviving claim becomes one node with its level + full provenance.
"""
import json, sys
from pathlib import Path
from collections import Counter

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P3B = ROOT / ".scribe-skills/phase3b"
OUT = ROOT / "knowledge/v2"
OUT.mkdir(parents=True, exist_ok=True)

# Load merged claims (each with attribution_list)
merged = {}
with (P3B / "merged_claims.jsonl").open() as f:
    for line in f:
        c = json.loads(line)
        merged[c["claim_id"]] = c

# Load all 12 classification verdicts
verdicts = {}
for p in sorted((P3B / "classify_batches").glob("scored_*.json")):
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            data = data.get("classifications") or list(data.values())[0]
        for r in data:
            verdicts[int(r["claim_id"])] = {"level": r["level"], "rationale": r.get("rationale", "")}
    except Exception as e:
        print(f"!! {p.name}: {e}", file=sys.stderr)

print(f"merged claims: {len(merged)}", file=sys.stderr)
print(f"verdicts:      {len(verdicts)}", file=sys.stderr)

# Build nodes
nodes = []
levels = Counter()
for cid, claim in merged.items():
    v = verdicts.get(cid)
    if not v:
        # No verdict — default L2
        level = "L2"
    else:
        level = v["level"]
    if level == "DROP":
        levels["DROP"] += 1
        continue
    levels[level] += 1
    nodes.append({
        "node_id": f"claim:{cid}",
        "level": level,
        "topic": claim["topic"],
        "text": claim["text"],
        "type": claim.get("type", "assertion"),
        "primary_speaker": claim.get("primary_speaker"),
        "speaker_term": claim.get("speaker_term"),
        "conditions": claim.get("conditions") or [],
        "mechanism": claim.get("mechanism"),
        "numbers": claim.get("numbers"),
        "bounded_by": claim.get("bounded_by") or [],
        "n_sources": claim.get("n_sources", 1),
        "attribution_list": claim.get("attribution_list", []),
        "level_rationale": v.get("rationale", "") if v else "",
    })

print(f"\nlevel distribution: {dict(levels)}", file=sys.stderr)

# Sort nodes: multi-source first (highest n_sources), then by topic
nodes.sort(key=lambda n: (-n["n_sources"], n["topic"]))

# Write nodes.jsonl
nodes_path = OUT / "nodes.jsonl"
with nodes_path.open("w") as f:
    for n in nodes:
        f.write(json.dumps(n) + "\n")
print(f"\nwrote {nodes_path.relative_to(ROOT)}  ({len(nodes)} nodes)", file=sys.stderr)

# Also load examples/practices/frameworks and append as additional nodes
def _load(p): return json.loads(p.read_text()) if p.exists() else []
examples = _load(P3B / "examples_pool.json")
practices = _load(P3B / "practices_pool.json")
frameworks = _load(P3B / "frameworks_pool.json")

with nodes_path.open("a") as f:
    for i, e in enumerate(examples):
        node = {
            "node_id": f"example:{i}",
            "level": "L3",
            "topic": e.get("topic", ""),
            "text": e.get("text", ""),
            "source_file": e.get("source_file"),
            "claim_index": e.get("claim_index", -1),
        }
        f.write(json.dumps(node) + "\n")
    for i, pr in enumerate(practices):
        node = {
            "node_id": f"practice:{i}",
            "level": "L4'",
            "topic": pr.get("topic", ""),
            "text": pr.get("text", ""),
            "source_file": pr.get("source_file"),
        }
        f.write(json.dumps(node) + "\n")
    for i, fw in enumerate(frameworks):
        node = {
            "node_id": f"framework:{i}",
            "level": "L1",
            "topic": fw.get("concept", ""),
            "name": fw.get("name", ""),
            "definition": fw.get("definition", ""),
            "source_file": fw.get("source_file"),
        }
        f.write(json.dumps(node) + "\n")

print(f"appended {len(examples)} examples + {len(practices)} practices + {len(frameworks)} frameworks", file=sys.stderr)

# Final count
total = len(nodes) + len(examples) + len(practices) + len(frameworks)
print(f"\nTOTAL nodes in nodes.jsonl: {total}", file=sys.stderr)
print(f"  L1 frameworks:  {len(frameworks)}", file=sys.stderr)
print(f"  L2/L2a/L2b claims: {len(nodes)}", file=sys.stderr)
print(f"  L3 examples:    {len(examples)}", file=sys.stderr)
print(f"  L4' practices:  {len(practices)}", file=sys.stderr)
