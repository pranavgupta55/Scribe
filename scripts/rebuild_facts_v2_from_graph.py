#!/usr/bin/env python3
"""Rebuild the `facts_v2` chroma collection so its IDs match graph_v2.json.

Motivation (see ADR 0009 + follow-up analysis 2026-07-26):

The original `facts_v2` was keyed on `merged_claims.jsonl` claim IDs (6055
rows). Phase 4 later re-indexed the surviving claims when exporting
`graph/graph_v2.json`, and only 840 of the 6055 chroma IDs collide by
integer with the graph's `claim:N` IDs — and even those 840 refer to
DIFFERENT semantic claims after re-index. In effect, ~100% of retrieved
facts have no valid graph lookup at query time, so the graph-augmented
expansion in `retrieval_v2.py` mostly no-ops.

This script rebuilds `facts_v2` from `knowledge/v2/nodes.jsonl` (which is
what phase 4 exported and what `graph/graph_v2.json` was built from). It
covers all four vector-searchable node kinds — claim, example, practice,
framework — 4191 rows total. `concept:` and `source:` nodes are excluded
(they are navigable via graph edges, not vector-searched).

Node IDs in the new collection are the graph's IDs verbatim (`claim:2290`,
`example:0`, `practice:0`, `framework:0`). Runtime lookup in
`_expand_fact` is O(1) hash lookup on `graph_v2.json` by construction.

Embedding model: `qwen3-embedding:8b` (same as before). Document is
`f"{label}: {description}"` — the same format `_node_text()` produces
for graph neighbors, so vector-search space matches expansion-node space.

Safety:
- Snapshots the current `facts_v2` to
  `.forge_scratch/scribe_rag_v2/facts_v2_legacy_snapshot.json` before
  deleting. The v1 `.chroma_v1_backup_*/` snapshot is untouched.
- `--dry-run` prints the plan and exits without touching chroma.

Usage:
  python3 -u scripts/rebuild_facts_v2_from_graph.py [--dry-run] [--force]
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("[fatal] `requests` not installed. pip install requests")
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent.resolve()
CHROMA_DIR = REPO_ROOT / ".chroma"
GRAPH_PATH = REPO_ROOT / "graph" / "graph_v2.json"
NODES_JSONL = REPO_ROOT / "knowledge" / "v2" / "nodes.jsonl"
SCRATCH = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2"
SNAPSHOT_OUT = SCRATCH / "facts_v2_legacy_snapshot.json"
ERROR_LOG = SCRATCH / "rebuild_facts_v2_from_graph_errors.log"

COLLECTION = "facts_v2"
EMBED_MODEL = "qwen3-embedding:8b"
OLLAMA_URL = "http://localhost:11434/api/embed"
# qwen3-embedding docs recommend prefixing the doc side with the same
# instruction the query side will use, so the vector spaces stay aligned.
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "

BATCH_SIZE = 8
BATCH_TIMEOUT_SEC = 90
PROGRESS_EVERY = 100

# Which graph node kinds go into facts_v2. Concepts + sources are
# navigable via graph edges, not vector-searched.
KEEP_PREFIXES = ("claim", "example", "practice", "framework")


def _embed_http(texts, timeout=BATCH_TIMEOUT_SEC):
    prefixed = [QWEN3_PREFIX + t for t in texts]
    resp = requests.post(
        OLLAMA_URL,
        json={"model": EMBED_MODEL, "input": prefixed, "keep_alive": "10m"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _embed_with_fallback(texts):
    try:
        return _embed_http(texts)
    except (requests.Timeout, requests.RequestException) as e:
        with ERROR_LOG.open("a") as f:
            f.write(f"batch fail ({len(texts)}): {e}\n")
        out = []
        for t in texts:
            try:
                out.append(_embed_http([t])[0])
            except (requests.Timeout, requests.RequestException) as e2:
                with ERROR_LOG.open("a") as f:
                    f.write(f"  item fail (len={len(t)}): {e2}\n")
                out.append(None)
        return out


def _node_doc(node: dict) -> str:
    """Match `_node_text()` in retrieval_v2.py so embedding space is aligned
    with what graph-expansion neighbors carry."""
    label = (node.get("label") or "").strip()
    desc = (node.get("description") or "").strip()
    if label and desc:
        return f"{label}: {desc}"
    return label or desc


def _build_metadata(graph_node: dict, jsonl_row: dict | None) -> dict:
    prefix = graph_node["id"].split(":", 1)[0]
    md: dict = {
        "node_id": graph_node["id"],
        "kind": prefix,
        "level": graph_node.get("level") or "",
        "topic": graph_node.get("topic") or graph_node.get("plugin") or "",
    }
    if jsonl_row:
        n_sources = jsonl_row.get("n_sources")
        if n_sources is not None:
            md["source_count"] = int(n_sources)
        attribution = jsonl_row.get("attribution_list")
        if attribution:
            md["attribution_json"] = json.dumps(attribution)
        # Claim-only extras — useful when the model needs the raw
        # conditions/mechanism/numbers without re-parsing the description.
        for k in ("conditions", "mechanism", "primary_speaker", "speaker_term"):
            v = jsonl_row.get(k)
            if isinstance(v, str) and v.strip():
                md[k] = v.strip()
    # Chroma metadata values must be JSON scalars (str/int/float/bool/None).
    md = {k: v for k, v in md.items() if v is not None and v != ""}
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print plan and exit")
    ap.add_argument("--force", action="store_true", help="skip confirmation prompt")
    args = ap.parse_args()

    print("=" * 72, flush=True)
    print("Rebuild facts_v2 from graph_v2.json + knowledge/v2/nodes.jsonl", flush=True)
    print("=" * 72, flush=True)

    if not GRAPH_PATH.exists():
        print(f"[fatal] missing {GRAPH_PATH}")
        sys.exit(1)
    if not NODES_JSONL.exists():
        print(f"[fatal] missing {NODES_JSONL}")
        sys.exit(1)

    # --- Load inputs -----------------------------------------------------
    print("[load] graph_v2.json", flush=True)
    with GRAPH_PATH.open() as f:
        graph = json.load(f)
    graph_by_id = {n["id"]: n for n in graph["nodes"]}

    print("[load] knowledge/v2/nodes.jsonl", flush=True)
    jsonl_by_id: dict[str, dict] = {}
    with NODES_JSONL.open() as f:
        for line in f:
            row = json.loads(line)
            nid = row.get("node_id")
            if nid:
                jsonl_by_id[nid] = row

    # --- Assemble work list ---------------------------------------------
    targets = [
        graph_by_id[nid] for nid in jsonl_by_id
        if nid in graph_by_id and nid.split(":", 1)[0] in KEEP_PREFIXES
    ]
    # Sort by (kind, id) for deterministic embedding order — helps if we
    # resume, and keeps the progress bar interpretable.
    def _sort_key(n):
        prefix, num = n["id"].split(":", 1)
        try:
            return (prefix, int(num))
        except ValueError:
            return (prefix, 10**9)
    targets.sort(key=_sort_key)

    print(f"[plan] {len(targets)} targets to embed:")
    from collections import Counter
    kinds = Counter(n["id"].split(":", 1)[0] for n in targets)
    for k, c in kinds.most_common():
        print(f"       {k:10s}  {c}")

    # Sample a doc to show length distribution
    sample_lens = sorted(len(_node_doc(n)) for n in targets)
    p50 = sample_lens[len(sample_lens)//2]
    p95 = sample_lens[int(len(sample_lens)*0.95)]
    mx = sample_lens[-1]
    print(f"[plan] doc-length p50={p50}, p95={p95}, max={mx}")

    if args.dry_run:
        print("[dry-run] no chroma writes. exiting.")
        return

    # Sanity ping Ollama
    print("[ollama] warm-up ping", flush=True)
    try:
        v = _embed_http(["health check"], timeout=20)
        print(f"[ollama] OK, dim={len(v[0])}", flush=True)
    except Exception as e:
        print(f"[fatal] ollama warm-up failed: {e}")
        print("        run:  ollama serve")
        sys.exit(1)

    # --- Snapshot old facts_v2 -------------------------------------------
    SCRATCH.mkdir(parents=True, exist_ok=True)
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        old = client.get_collection(COLLECTION)
        old_ct = old.count()
        print(f"[snapshot] old facts_v2 has {old_ct} rows", flush=True)
        if SNAPSHOT_OUT.exists() and SNAPSHOT_OUT.stat().st_size > 100:
            print(f"[snapshot] reusing existing {SNAPSHOT_OUT.name}", flush=True)
        else:
            got = old.get(include=["documents", "metadatas"])
            SNAPSHOT_OUT.write_text(json.dumps({
                "ids": got["ids"],
                "docs": got["documents"],
                "metas": got["metadatas"],
                "snapshot_at": None,  # avoid Date.now()
                "note": "pre-rebuild snapshot of facts_v2 keyed on merged_claims.jsonl IDs",
            }))
            print(f"[snapshot] wrote {SNAPSHOT_OUT} ({SNAPSHOT_OUT.stat().st_size // 1024}KB)")
    except Exception as e:
        print(f"[snapshot] no existing facts_v2 collection to snapshot ({e})")

    if not args.force:
        confirm = input("Proceed to drop + rebuild facts_v2? [yes/N] ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("[abort] user declined")
            return

    # --- Drop + recreate -------------------------------------------------
    try:
        client.delete_collection(COLLECTION)
        print(f"[chroma] dropped existing {COLLECTION}", flush=True)
    except Exception:
        pass
    coll = client.create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    print(f"[chroma] created fresh {COLLECTION}", flush=True)

    # --- Embed + insert --------------------------------------------------
    start = time.time()
    written = 0
    skipped = 0
    total = len(targets)

    for start_i in range(0, total, BATCH_SIZE):
        end_i = min(start_i + BATCH_SIZE, total)
        batch = targets[start_i:end_i]
        batch_ids = [n["id"] for n in batch]
        batch_docs = [_node_doc(n) for n in batch]
        batch_metas = [_build_metadata(n, jsonl_by_id.get(n["id"])) for n in batch]

        embs = _embed_with_fallback(batch_docs)

        keep_ids, keep_docs, keep_metas, keep_embs = [], [], [], []
        for i, e in enumerate(embs):
            if e is not None:
                keep_ids.append(batch_ids[i])
                keep_docs.append(batch_docs[i])
                keep_metas.append(batch_metas[i])
                keep_embs.append(e)
            else:
                skipped += 1

        if keep_ids:
            try:
                coll.add(
                    ids=keep_ids,
                    documents=keep_docs,
                    metadatas=keep_metas,
                    embeddings=keep_embs,
                )
                written += len(keep_ids)
            except Exception as e:
                with ERROR_LOG.open("a") as f:
                    f.write(f"chroma add fail (batch {start_i}): {e}\n")

        seen = end_i
        if seen % PROGRESS_EVERY == 0 or seen == total:
            elapsed = time.time() - start
            rate = seen / elapsed if elapsed > 0 else 0.0
            eta = (total - seen) / rate if rate > 0 else 0
            print(
                f"[facts_v2] {seen}/{total} written={written} skipped={skipped} "
                f"({rate:.1f}/s, ETA {eta:.0f}s)",
                flush=True,
            )

    final_count = coll.count()
    print("", flush=True)
    print("=" * 72, flush=True)
    print(f"facts_v2: {final_count} rows written, {skipped} skipped")
    print(f"Total time: {(time.time()-start)/60:.1f} min")
    if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0:
        print(f"[note] errors logged in {ERROR_LOG}")
    print("=" * 72, flush=True)

    # --- Quick alignment sanity check ------------------------------------
    got = coll.get(include=[])
    new_ids = set(got["ids"])
    graph_ids = {n["id"] for n in graph["nodes"] if n["id"].split(":", 1)[0] in KEEP_PREFIXES}
    intersection = new_ids & graph_ids
    print(f"[alignment] facts_v2 IDs in graph: {len(intersection)}/{len(new_ids)} "
          f"(target 100%)")
    if new_ids - graph_ids:
        print(f"[alignment] {len(new_ids - graph_ids)} rows have no graph node (unexpected)")
    if graph_ids - new_ids:
        print(f"[alignment] {len(graph_ids - new_ids)} graph nodes not embedded "
              f"(expected only if some failed)")


if __name__ == "__main__":
    main()
