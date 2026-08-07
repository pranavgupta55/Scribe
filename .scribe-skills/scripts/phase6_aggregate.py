#!/usr/bin/env python3
"""Phase 6 (Wave 3) aggregation — compute pass rate from scored_*.json, compare
to the Wave 2 baseline (87.2%), and measure Haiku↔Sonnet auditor agreement."""
import json, glob, os
from collections import Counter, defaultdict

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "phase6/wave3")

def load_scores(pattern):
    rows = {}
    for p in sorted(glob.glob(os.path.join(DIR, pattern))):
        try:
            for r in json.load(open(p)):
                rows[r["node_id"]] = r
        except Exception as e:
            print(f"  ! {os.path.basename(p)}: {e}")
    return rows

haiku = load_scores("scored_*.json")
sonnet = load_scores("audited_*.json")

by_level = defaultdict(lambda: [0, 0])   # level -> [passing, total]
overall = [0, 0]
fm = Counter()
for nid, r in haiku.items():
    lvl = r.get("level", "?")
    ok = bool(r.get("passing"))
    by_level[lvl][1] += 1; by_level[lvl][0] += ok
    overall[1] += 1; overall[0] += ok
    for m in r.get("failure_modes", []): fm[m] += 1

print(f"=== Phase 6 Wave 3 — Haiku scoring ({overall[1]} claims) ===")
rate = 100 * overall[0] / overall[1] if overall[1] else 0
print(f"OVERALL pass rate: {overall[0]}/{overall[1]} = {rate:.1f}%   (Wave 2 baseline: 87.2%)")
for lvl in sorted(by_level):
    p, t = by_level[lvl]
    print(f"  {lvl}: {p}/{t} = {100*p/t:.1f}%")
print(f"top failure modes: {dict(fm.most_common(8))}")

# Haiku vs Sonnet auditor agreement on the audited subset
if sonnet:
    agree = disagree = 0
    flips = []
    for nid, s in sonnet.items():
        if nid in haiku:
            if bool(s.get("passing")) == bool(haiku[nid].get("passing")): agree += 1
            else:
                disagree += 1
                flips.append((nid, haiku[nid].get("passing"), s.get("passing")))
    tot = agree + disagree
    print(f"\n=== Sonnet auditor second-opinion ({tot} overlap) ===")
    print(f"agreement: {agree}/{tot} = {100*agree/tot:.1f}%" if tot else "no overlap")
    for nid, h, so in flips[:10]:
        print(f"  FLIP {nid}: haiku_pass={h} -> sonnet_pass={so}")

summary = {
    "wave": 3, "sample_n": overall[1], "pass_rate": round(rate, 1),
    "baseline_wave2": 87.2,
    "by_level": {k: {"passing": v[0], "total": v[1]} for k, v in by_level.items()},
    "top_failure_modes": dict(fm.most_common(12)),
    "sonnet_overlap": len(sonnet),
}
json.dump(summary, open(os.path.join(DIR, "eval_summary.json"), "w"), indent=1)
print(f"\nwrote {DIR}/eval_summary.json")
