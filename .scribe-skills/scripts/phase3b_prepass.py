#!/usr/bin/env python3
"""Phase 3b mechanical pre-pass:
  1. Aggregate all 204 source extractions into one claims pool.
  2. Embed every claim text via qwen3-embedding:8b.
  3. For each pair on the SAME canonical topic with cos sim ≥ 0.70, emit as
     candidate merge (the 0.85+ are auto-merge confidence; 0.70-0.85 send to
     Haiku for judgment).
  4. Split the resulting (claim_pool + candidate_pairs) into 12 batches for
     Phase 3b Haiku verification agents.

Outputs:
  .scribe-skills/phase3b/claims_pool.jsonl
  .scribe-skills/phase3b/embeddings.npy
  .scribe-skills/phase3b/candidate_pairs.json
  .scribe-skills/phase3b/batches/batch_{01..12}.json
"""
import json, sys, time
from pathlib import Path
from collections import defaultdict, Counter

import numpy as np

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
EXT = ROOT / ".scribe-skills/phase3a/extracted"
OUT = ROOT / ".scribe-skills/phase3b"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "batches").mkdir(parents=True, exist_ok=True)

EMBED_MODEL = "qwen3-embedding:8b"
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "
SIM_HIGH = 0.85   # auto-merge candidate
SIM_LOW = 0.70    # Haiku-judge threshold
N_BATCHES = 12


def main():
    # 1. Build the master claims pool
    claims = []          # list of {claim_id, source, topic, text, ...orig fields...}
    examples = []        # list of examples (kept as side data, attached after merge)
    practices = []
    frameworks = []

    files = sorted(EXT.glob("*.json"))
    print(f"loading {len(files)} extractions...", file=sys.stderr)
    for p in files:
        try:
            d = json.loads(p.read_text())
        except Exception as e:
            print(f"!! {p.name}: {e}", file=sys.stderr)
            continue
        src = d.get("source_file", p.name)
        primary_speaker = d.get("primary_speaker", "")
        for c in d.get("claims", []) or []:
            cid = len(claims)
            claims.append({
                "claim_id": cid,
                "source_file": src,
                "primary_speaker": primary_speaker,
                "topic": c.get("topic", ""),
                "text": c.get("text", ""),
                "type": c.get("type", "assertion"),
                "speaker": c.get("speaker", primary_speaker),
                "speaker_term": c.get("speaker_term"),
                "conditions": c.get("conditions", []) or [],
                "mechanism": c.get("mechanism"),
                "numbers": c.get("numbers"),
                "bounded_by": c.get("bounded_by", []) or [],
            })
        for e in d.get("examples", []) or []:
            examples.append({"source_file": src, **e})
        for pr in d.get("practices", []) or []:
            practices.append({"source_file": src, **pr})
        for f in d.get("frameworks", []) or []:
            frameworks.append({"source_file": src, **f})

    print(f"  claims:      {len(claims)}", file=sys.stderr)
    print(f"  examples:    {len(examples)}", file=sys.stderr)
    print(f"  practices:   {len(practices)}", file=sys.stderr)
    print(f"  frameworks:  {len(frameworks)}", file=sys.stderr)

    # Write claims pool
    pool_path = OUT / "claims_pool.jsonl"
    with pool_path.open("w") as f:
        for c in claims:
            f.write(json.dumps(c) + "\n")
    (OUT / "examples_pool.json").write_text(json.dumps(examples, indent=1))
    (OUT / "practices_pool.json").write_text(json.dumps(practices, indent=1))
    (OUT / "frameworks_pool.json").write_text(json.dumps(frameworks, indent=1))

    # 2. Embed all claims via qwen3-embedding:8b
    emb_path = OUT / "embeddings.npy"
    if emb_path.exists() and "--force-embed" not in sys.argv:
        print(f"  loading cached embeddings from {emb_path.name}", file=sys.stderr)
        A = np.load(emb_path)
        if A.shape[0] != len(claims):
            print(f"  cached embeddings stale ({A.shape[0]} vs {len(claims)}); re-embed", file=sys.stderr)
            A = embed_all(claims)
            np.save(emb_path, A)
    else:
        A = embed_all(claims)
        np.save(emb_path, A)

    # L2-normalize for cosine
    A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
    print(f"  embeddings shape: {A.shape}", file=sys.stderr)

    # 3. Find candidate pairs within same canonical topic
    by_topic = defaultdict(list)
    for i, c in enumerate(claims):
        if c["topic"]:
            by_topic[c["topic"]].append(i)

    print(f"\nfinding candidate paraphrase pairs (sim ≥ {SIM_LOW}) within same topic...", file=sys.stderr)
    auto_merge = []     # sim >= 0.85
    judge_pairs = []    # 0.70 <= sim < 0.85
    for topic, idxs in by_topic.items():
        if len(idxs) < 2:
            continue
        sub = A[idxs]
        sim = sub @ sub.T
        np.fill_diagonal(sim, -1)
        # Upper triangle pairs only (i < j)
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                s = float(sim[ii, jj])
                if s < SIM_LOW:
                    continue
                a, b = idxs[ii], idxs[jj]
                # Skip pairs from same source — same-source duplicates already filtered upstream
                if claims[a]["source_file"] == claims[b]["source_file"]:
                    continue
                pair = {
                    "a_id": a, "b_id": b, "topic": topic,
                    "sim": round(s, 3),
                    "a_text": claims[a]["text"][:300],
                    "b_text": claims[b]["text"][:300],
                    "a_source": claims[a]["source_file"],
                    "b_source": claims[b]["source_file"],
                }
                if s >= SIM_HIGH:
                    auto_merge.append(pair)
                else:
                    judge_pairs.append(pair)

    print(f"  auto-merge candidates (sim ≥ {SIM_HIGH}): {len(auto_merge)}", file=sys.stderr)
    print(f"  judge-needed pairs ({SIM_LOW} ≤ sim < {SIM_HIGH}): {len(judge_pairs)}", file=sys.stderr)

    (OUT / "candidate_pairs.json").write_text(json.dumps({
        "auto_merge": auto_merge,
        "judge_pairs": judge_pairs,
    }, indent=1))

    # 4. Split judge_pairs into N_BATCHES for Haiku judgment.
    # Per agent: ~equal share; aim 100-200 pairs per agent.
    if len(judge_pairs) == 0:
        print("\nno judge pairs needed; skipping batch creation", file=sys.stderr)
        return
    per_batch = max(1, (len(judge_pairs) + N_BATCHES - 1) // N_BATCHES)
    for i in range(N_BATCHES):
        chunk = judge_pairs[i * per_batch : (i + 1) * per_batch]
        if not chunk:
            break
        # Attach the FULL claim records so the agent has all context for classification
        enriched = []
        for p in chunk:
            enriched.append({
                **p,
                "a_full": claims[p["a_id"]],
                "b_full": claims[p["b_id"]],
            })
        bp = OUT / "batches" / f"batch_{i+1:02d}.json"
        bp.write_text(json.dumps({"batch_id": i + 1, "pairs": enriched}, indent=1))
        print(f"  wrote {bp.relative_to(ROOT)}  ({len(chunk)} pairs)", file=sys.stderr)


def embed_all(claims):
    import ollama
    print(f"\nembedding {len(claims)} claims via {EMBED_MODEL}...", file=sys.stderr)
    vectors = []
    t0 = time.time()
    for i, c in enumerate(claims):
        text = QWEN3_PREFIX + (c["text"] or c["topic"])
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text)
        v = resp.get("embedding") or resp.get("embeddings")
        vectors.append(v)
        if (i + 1) % 200 == 0 or i + 1 == len(claims):
            elapsed = time.time() - t0
            rate = (i + 1) / max(1e-3, elapsed)
            eta = (len(claims) - i - 1) / max(1e-3, rate)
            print(f"  embed {i+1}/{len(claims)}  ({rate:.1f}/s, ETA {eta:.0f}s)", file=sys.stderr)
    return np.asarray(vectors, dtype=np.float32)


if __name__ == "__main__":
    main()
