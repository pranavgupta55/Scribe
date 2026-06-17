#!/usr/bin/env python3
"""Aggregate the 23 Phase 1a verdict files into a single concepts.json.

Inputs:
  - .scribe-skills/phase1a/clusters.json (original Ward output)
  - .scribe-skills/phase1a/verify_batches/scored_{01..20}.json (Haiku verdicts)
  - .scribe-skills/phase1a/verify_batches/scored_hard_{1..3}.json (Sonnet verdicts)

Output:
  - .scribe-skills/phase1a/concepts.draft.json — preliminary concept list before Phase 1b
  - .scribe-skills/phase1a/concepts_review_queue.json — kicked members that need re-clustering

Apply rules per verdict:
  - accept     → keep cluster as-is; canonical_name from agent's `canonical_name`
  - rename     → keep members, swap canonical_name
  - kick       → reduce members; kicked members go to review queue
  - split      → produce N sub-clusters (each with its own canonical_name + members + definition)
  - merge_with → defer: keep cluster but mark merge_target; final pass resolves
"""
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P1A = ROOT / ".scribe-skills/phase1a"
BATCH = P1A / "verify_batches"

# 1) Load original clusters
orig = json.loads((P1A / "clusters.json").read_text())
orig_clusters = {c["cluster_id"]: c for c in orig["clusters"]}

# 2) Load all verdicts
verdicts_by_cluster = {}
for p in sorted(BATCH.glob("scored_*.json")):
    try:
        records = json.loads(p.read_text())
        if not isinstance(records, list):
            records = records.get("verdicts") or records.get("clusters") or [records]
        for r in records:
            cid = r.get("cluster_id")
            if cid is None:
                continue
            verdicts_by_cluster[int(cid)] = r
    except Exception as e:
        print(f"!! {p.name}: {e}", file=sys.stderr)

print(f"loaded verdicts for {len(verdicts_by_cluster)}/{len(orig_clusters)} clusters", file=sys.stderr)
missing = set(orig_clusters) - set(verdicts_by_cluster)
if missing:
    print(f"  missing verdicts: {sorted(missing)[:10]}{'...' if len(missing)>10 else ''}", file=sys.stderr)

# 3) Tally verdict types
tally = Counter(v.get("verdict","?") for v in verdicts_by_cluster.values())
print("verdict distribution:", dict(tally), file=sys.stderr)

# 4) Apply verdicts → produce concept records
concepts = []
review_queue = []          # kicked members or missing-verdict clusters
next_concept_id = 0

def make_concept(name, members, super_cluster_id, definition, source_cluster_ids, role="domain"):
    global next_concept_id
    c = {
        "concept_id": next_concept_id,
        "canonical_name": name.strip() if name else "(unnamed)",
        "members": [m if isinstance(m, str) else m.get("topic", "") for m in members],
        "aliases": [m if isinstance(m, str) else m.get("topic", "") for m in members if (m if isinstance(m, str) else m.get("topic","")) != name],
        "definition": (definition or "").strip(),
        "super_cluster_id": super_cluster_id,
        "source_cluster_ids": source_cluster_ids,
        "role": role,
    }
    next_concept_id += 1
    return c

# First pass: handle accept / rename / kick / split (i.e. non-merge)
merge_pending = {}     # cluster_id → target cluster_id (resolve in pass 2)

for cid, cluster in orig_clusters.items():
    v = verdicts_by_cluster.get(cid)
    member_topics = [m["topic"] for m in cluster["members"]]
    super_cluster_id = cluster["super_cluster_id"]

    if v is None:
        # No verdict — fall back to accept proposed_canonical
        concepts.append(make_concept(
            cluster["proposed_canonical"], member_topics, super_cluster_id,
            f"({len(member_topics)} members from Ward cluster — no verdict)",
            [cid]))
        continue

    verdict = v.get("verdict", "accept")
    canonical = v.get("canonical_name") or cluster["proposed_canonical"]
    definition = v.get("definition") or ""

    if verdict == "accept" or verdict == "rename":
        concepts.append(make_concept(canonical, member_topics, super_cluster_id, definition, [cid]))

    elif verdict == "kick":
        # kick_members may be ["topic str", ...] or [{"topic": ...}, ...]
        raw_kicked = v.get("kick_members") or []
        kicked = set()
        for k in raw_kicked:
            if isinstance(k, str):
                kicked.add(k)
            elif isinstance(k, dict):
                kicked.add(k.get("topic") or k.get("name") or "")
        keep = [m for m in member_topics if m not in kicked]
        concepts.append(make_concept(canonical, keep, super_cluster_id, definition, [cid]))
        for k in kicked:
            review_queue.append({
                "topic": k,
                "reason": f"kicked from cluster {cid} ({canonical})",
                "original_cluster_id": cid,
                "rationale": v.get("rationale", "")
            })

    elif verdict == "split":
        splits = v.get("splits") or []
        if not splits:
            # Malformed split — fall back to accept
            concepts.append(make_concept(canonical, member_topics, super_cluster_id, definition, [cid]))
            continue
        for s in splits:
            sub_name = s.get("canonical_name") or "(unnamed split)"
            sub_members = s.get("members") or []
            sub_def = s.get("definition") or ""
            concepts.append(make_concept(sub_name, sub_members, super_cluster_id, sub_def, [cid]))

    elif verdict == "merge_with":
        target = v.get("merge_target_id")
        if target is None:
            concepts.append(make_concept(canonical, member_topics, super_cluster_id, definition, [cid]))
        else:
            merge_pending[cid] = int(target)
            # Still create a placeholder concept so we have its members; we'll fold in pass 2.
            concepts.append(make_concept(canonical, member_topics, super_cluster_id, definition, [cid]))
    else:
        # Unknown verdict — treat as accept
        concepts.append(make_concept(canonical, member_topics, super_cluster_id, definition, [cid]))

# Pass 2: resolve merges. Find each merge source's concept(s) and fold into target's concept(s).
# Mapping: cluster_id → concept_ids (a cluster could have produced multiple via split)
cluster_to_concepts = defaultdict(list)
for c in concepts:
    for cid in c["source_cluster_ids"]:
        cluster_to_concepts[cid].append(c["concept_id"])

absorbed = set()
for src_cid, tgt_cid in merge_pending.items():
    src_concept_ids = cluster_to_concepts.get(src_cid, [])
    tgt_concept_ids = cluster_to_concepts.get(tgt_cid, [])
    if not src_concept_ids or not tgt_concept_ids:
        continue
    # Fold first src concept into first tgt concept (simple model)
    src = next((c for c in concepts if c["concept_id"] == src_concept_ids[0]), None)
    tgt = next((c for c in concepts if c["concept_id"] == tgt_concept_ids[0]), None)
    if src and tgt and src["concept_id"] != tgt["concept_id"]:
        # Union members/aliases
        for m in src["members"]:
            if m not in tgt["members"]:
                tgt["members"].append(m)
        for a in src["aliases"]:
            if a not in tgt["aliases"] and a != tgt["canonical_name"]:
                tgt["aliases"].append(a)
        if src_cid not in tgt["source_cluster_ids"]:
            tgt["source_cluster_ids"].append(src_cid)
        absorbed.add(src["concept_id"])

concepts = [c for c in concepts if c["concept_id"] not in absorbed]
# Renumber for cleanliness
for i, c in enumerate(concepts):
    c["concept_id"] = i

print(f"\nfinal concepts: {len(concepts)} (from {len(orig_clusters)} Ward clusters)", file=sys.stderr)
print(f"review queue (kicked/orphan): {len(review_queue)} members", file=sys.stderr)

# Member-count distribution
sizes = [len(c["members"]) for c in concepts]
print(f"member counts: min={min(sizes)} median={sorted(sizes)[len(sizes)//2]} "
      f"mean={sum(sizes)/len(sizes):.1f} max={max(sizes)}", file=sys.stderr)
singletons = sum(1 for s in sizes if s == 1)
print(f"singletons: {singletons} ({100*singletons/len(sizes):.1f}%)", file=sys.stderr)

# Write outputs
(P1A / "concepts.draft.json").write_text(json.dumps({
    "n_concepts": len(concepts),
    "concepts": concepts,
}, indent=2))
(P1A / "concepts_review_queue.json").write_text(json.dumps({
    "n_review": len(review_queue),
    "members": review_queue,
}, indent=2))
print(f"\nwrote {(P1A/'concepts.draft.json').relative_to(ROOT)}", file=sys.stderr)
print(f"wrote {(P1A/'concepts_review_queue.json').relative_to(ROOT)}", file=sys.stderr)
