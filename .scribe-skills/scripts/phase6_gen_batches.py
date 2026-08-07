#!/usr/bin/env python3
"""Phase 6 (Wave 3) quality-eval batch generator.

Samples ~200 L2/L2a/L2b claim nodes from knowledge/v2/nodes.jsonl (the authoritative
claim data that graph_v2.json renders — node_ids match the graph exactly), stratified
proportional to the L2/L2a/L2b distribution. Splits into 16 Haiku scoring batches, and
carves a 40-claim audit subset into 4 Sonnet auditor batches for an independent second
opinion (Haiku/Sonnet agreement measurement + false-pass detection).

Output: .scribe-skills/phase6/wave3/{batch_NN.json, audit_NN.json}
Deterministic (fixed seed) so re-runs reproduce the same sample.

Batch/claim shape matches phase6/batches/batch_01.json:
  {batch_id, kind, claims:[{node_id, level, topic, text, type, speaker, speaker_term,
                            conditions, mechanism, numbers, bounded_by, n_sources}]}
"""
import json, random, os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODES = os.path.join(ROOT, "knowledge/v2/nodes.jsonl")
OUT   = os.path.join(ROOT, ".scribe-skills/phase6/wave3")
os.makedirs(OUT, exist_ok=True)

SAMPLE_N       = 200   # total claims scored by Haiku
HAIKU_BATCHES  = 16
AUDIT_N        = 40    # subset re-scored by Sonnet auditors
SONNET_BATCHES = 4
SEED           = 20260807

random.seed(SEED)

# ---- load claim nodes ----
by_level = defaultdict(list)
for line in open(NODES):
    d = json.loads(line)
    if d.get("level") in ("L2", "L2a", "L2b"):
        by_level[d["level"]].append(d)

total = sum(len(v) for v in by_level.values())
print(f"claim universe: {total} ({ {k: len(v) for k, v in by_level.items()} })")

# ---- proportional stratified sample ----
sample = []
for level, nodes in sorted(by_level.items()):
    k = round(SAMPLE_N * len(nodes) / total)
    k = min(k, len(nodes))
    sample.extend(random.sample(nodes, k))
random.shuffle(sample)
print(f"sampled {len(sample)} ({dict(Counter(n['level'] for n in sample))})")

def slim(d):
    return {
        "node_id":      d["node_id"],
        "level":        d["level"],
        "topic":        d.get("topic", ""),
        "text":         d.get("text", ""),
        "type":         d.get("type", ""),
        "speaker":      d.get("primary_speaker", ""),
        "speaker_term": d.get("speaker_term", ""),
        "conditions":   d.get("conditions", []),
        "mechanism":    d.get("mechanism"),
        "numbers":      d.get("numbers"),
        "bounded_by":   d.get("bounded_by", []),
        "n_sources":    d.get("n_sources"),
    }

# ---- split into Haiku batches ----
def chunk(lst, n):
    size = (len(lst) + n - 1) // n
    return [lst[i:i + size] for i in range(0, len(lst), size)]

haiku_chunks = chunk(sample, HAIKU_BATCHES)
for i, ch in enumerate(haiku_chunks, 1):
    p = os.path.join(OUT, f"batch_{i:02d}.json")
    json.dump({"batch_id": i, "kind": "haiku-score", "claims": [slim(d) for d in ch]},
              open(p, "w"), indent=1)
print(f"wrote {len(haiku_chunks)} Haiku batches -> {OUT}/batch_NN.json")

# ---- audit subset for Sonnet second-opinion ----
audit = random.sample(sample, min(AUDIT_N, len(sample)))
audit_chunks = chunk(audit, SONNET_BATCHES)
for i, ch in enumerate(audit_chunks, 1):
    p = os.path.join(OUT, f"audit_{i:02d}.json")
    json.dump({"batch_id": i, "kind": "sonnet-audit", "claims": [slim(d) for d in ch]},
              open(p, "w"), indent=1)
print(f"wrote {len(audit_chunks)} Sonnet audit batches -> {OUT}/audit_NN.json")

# manifest for the workflow
manifest = {
    "haiku_batches":  [os.path.join(OUT, f"batch_{i:02d}.json") for i in range(1, len(haiku_chunks) + 1)],
    "sonnet_batches": [os.path.join(OUT, f"audit_{i:02d}.json") for i in range(1, len(audit_chunks) + 1)],
    "sample_n": len(sample), "audit_n": len(audit), "seed": SEED,
    "level_dist": dict(Counter(n["level"] for n in sample)),
}
json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
print("wrote manifest.json")
