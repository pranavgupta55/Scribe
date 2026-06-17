#!/usr/bin/env python3
"""Aggregate 8 Phase 1b Sonnet scored files → final knowledge/concepts.json.

Inputs:
  - .scribe-skills/phase1a/concepts.cleaned.json    (pre-Phase-1b state, 383 concepts)
  - .scribe-skills/phase1b/batches/scored_{01..08}.json (Sonnet verdicts)

Output:
  - knowledge/concepts.json                          (canonical concept list)
  - .scribe-skills/phase1b/orphans.json              (true orphans for later reconciliation)
  - .scribe-skills/phase1b/merges_applied.json       (audit trail)

Apply rules:
  1. For each concept: take the Sonnet-refined fields (canonical_name, aliases, avoid_terms,
     definition, ambiguity_notes, role) over the Phase 1a draft fields.
  2. For each kicked_assignment with assigned_to=<int>: append the topic to that concept's
     aliases (deduplicated).
  3. For each kicked_assignment with assigned_to="ORPHAN": collect into orphan list.
  4. For each in_batch_merge: fold source concept into target concept (union members/aliases,
     drop source concept). Apply in batch order, last write wins on canonical_name.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
P1A = ROOT / ".scribe-skills/phase1a"
P1B = ROOT / ".scribe-skills/phase1b"
OUT = ROOT / "knowledge/concepts.json"

# Load Phase 1a draft state — provides member topics + super_cluster_id + source_cluster_ids
draft = json.loads((P1A / "concepts.cleaned.json").read_text())
draft_by_id = {c["concept_id"]: c for c in draft["concepts"]}

# Load all 8 Phase 1b scored files
all_refined = {}     # concept_id → refined record
kicked_assignments = []
in_batch_merges = []

for i in range(1, 9):
    p = P1B / "batches" / f"scored_{i:02d}.json"
    if not p.exists():
        print(f"!! missing {p.name}", file=sys.stderr)
        continue
    data = json.loads(p.read_text())
    for c in data.get("concepts", []):
        all_refined[int(c["concept_id"])] = c
    kicked_assignments.extend(data.get("kicked_assignments", []))
    in_batch_merges.extend(data.get("in_batch_merges", []))

print(f"loaded {len(all_refined)} refined concepts, "
      f"{len(kicked_assignments)} kicked assignments, "
      f"{len(in_batch_merges)} in-batch merges", file=sys.stderr)

# Build the final concept list
final = {}   # concept_id → final record (post-merge)
for cid, draft_c in draft_by_id.items():
    refined = all_refined.get(cid)
    if refined is None:
        # No Phase 1b critique — fall back to draft state
        final[cid] = {
            "concept_id": cid,
            "canonical_name": draft_c["canonical_name"],
            "aliases": list(draft_c["aliases"]),
            "avoid_terms": [],
            "definition": draft_c["definition"],
            "ambiguity_notes": None,
            "role": "domain",
            "members": list(draft_c["members"]),
            "super_cluster_id": draft_c["super_cluster_id"],
            "source_cluster_ids": list(draft_c["source_cluster_ids"]),
        }
    else:
        final[cid] = {
            "concept_id": cid,
            "canonical_name": refined.get("canonical_name") or draft_c["canonical_name"],
            "aliases": refined.get("aliases") or list(draft_c["aliases"]),
            "avoid_terms": refined.get("avoid_terms") or [],
            "definition": refined.get("definition") or draft_c["definition"],
            "ambiguity_notes": refined.get("ambiguity_notes"),
            "role": refined.get("role") or "domain",
            "members": list(draft_c["members"]),
            "super_cluster_id": draft_c["super_cluster_id"],
            "source_cluster_ids": list(draft_c["source_cluster_ids"]),
        }

# Apply kicked assignments — add each topic to its assigned concept's aliases (and members)
orphans = []
assigned_count = 0
for ka in kicked_assignments:
    topic = (ka.get("topic") or "").strip()
    assigned_to = ka.get("assigned_to")
    if not topic:
        continue
    if assigned_to == "ORPHAN" or assigned_to is None:
        orphans.append({
            "topic": topic,
            "rationale": ka.get("rationale", ""),
        })
        continue
    try:
        tgt_id = int(assigned_to)
    except (ValueError, TypeError):
        orphans.append({"topic": topic, "rationale": f"unparseable assigned_to={assigned_to!r}"})
        continue
    if tgt_id in final:
        c = final[tgt_id]
        if topic not in c["aliases"] and topic != c["canonical_name"]:
            c["aliases"].append(topic)
        if topic not in c["members"]:
            c["members"].append(topic)
        assigned_count += 1
    else:
        orphans.append({"topic": topic, "rationale": f"assigned to nonexistent concept_id={tgt_id}"})

print(f"applied {assigned_count} kicked assignments; {len(orphans)} orphans", file=sys.stderr)

# Apply in-batch merges — fold source concept into target
absorbed = set()
applied_merges = []
for m in in_batch_merges:
    try:
        src_id = int(m.get("source_concept_id"))
        tgt_id = int(m.get("target_concept_id"))
    except (ValueError, TypeError):
        continue
    if src_id in absorbed or tgt_id in absorbed:
        continue
    if src_id not in final or tgt_id not in final:
        continue
    if src_id == tgt_id:
        continue
    src = final[src_id]
    tgt = final[tgt_id]
    # Union members + aliases
    for member in src["members"]:
        if member not in tgt["members"]:
            tgt["members"].append(member)
    for alias in src["aliases"]:
        if alias not in tgt["aliases"] and alias != tgt["canonical_name"]:
            tgt["aliases"].append(alias)
    # Also fold src's canonical_name as an alias if it's different from target's
    if src["canonical_name"] != tgt["canonical_name"] and src["canonical_name"] not in tgt["aliases"]:
        tgt["aliases"].append(src["canonical_name"])
    # Union source_cluster_ids
    for cid in src["source_cluster_ids"]:
        if cid not in tgt["source_cluster_ids"]:
            tgt["source_cluster_ids"].append(cid)
    absorbed.add(src_id)
    applied_merges.append({"source": src_id, "target": tgt_id, "rationale": m.get("rationale", "")})

for sid in absorbed:
    del final[sid]

print(f"applied {len(applied_merges)} in-batch merges", file=sys.stderr)

# Final renumbering
ordered = sorted(final.values(), key=lambda c: c["concept_id"])
for i, c in enumerate(ordered):
    c["concept_id"] = i

# Dedup aliases (lower-case)
for c in ordered:
    seen = set()
    deduped = []
    for a in c["aliases"]:
        k = a.lower().strip()
        if k and k not in seen and a != c["canonical_name"]:
            seen.add(k)
            deduped.append(a)
    c["aliases"] = deduped

# Stats
sizes = [len(c["members"]) for c in ordered]
roles_count = defaultdict(int)
for c in ordered:
    roles_count[c.get("role", "domain")] += 1

print(f"\nfinal concept count: {len(ordered)}", file=sys.stderr)
print(f"member-count: min={min(sizes)} median={sorted(sizes)[len(sizes)//2]} "
      f"mean={sum(sizes)/len(sizes):.1f} max={max(sizes)}", file=sys.stderr)
print(f"roles: {dict(roles_count)}", file=sys.stderr)
print(f"orphans queued: {len(orphans)}", file=sys.stderr)

# Write outputs
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps({
    "version": "v2-graph-rebuild",
    "n_concepts": len(ordered),
    "n_orphans": len(orphans),
    "concepts": ordered,
}, indent=2))
(P1B / "orphans.json").write_text(json.dumps({
    "n_orphans": len(orphans),
    "orphans": orphans,
}, indent=2))
(P1B / "merges_applied.json").write_text(json.dumps({
    "n_merges": len(applied_merges),
    "merges": applied_merges,
}, indent=2))
print(f"\nwrote {OUT.relative_to(ROOT)}", file=sys.stderr)
print(f"wrote {(P1B/'orphans.json').relative_to(ROOT)}", file=sys.stderr)
print(f"wrote {(P1B/'merges_applied.json').relative_to(ROOT)}", file=sys.stderr)
