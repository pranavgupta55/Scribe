#!/usr/bin/env python3
"""Resume the chunks_v2 phase of the v2 ChromaDB rebuild.

Two modes:
  - default (fresh): drops partial chunks_v2 and rebuilds from scratch.
  - --resume       : reads existing chunks_v2 IDs and skips them, only
                     embedding the missing ones.

Reads the v1 chunks snapshot from `.chroma_v1_backup_*/` (newest) and
re-embeds each chunk with qwen3-embedding:8b.

Key hardening:
  - Per-chunk truncation to MAX_INPUT_CHARS (~7500). qwen3-embedding:8b
    is served under llama-server with -c 4096 tokens; chunks over ~12k chars
    hang the server silently.
  - Batch size 2. If any batch fails, we drop 2 items at most.
  - Direct HTTP with 60s per-batch timeout via requests, not ollama-python.
    Ollama's Python client can hang indefinitely when the underlying
    llama-server dies mid-request; a short timeout makes recovery deterministic.
  - On timeout, retry once with per-item embeds; then skip and log.

Usage:
  # Fresh:    python3 -u scripts/rebuild_chroma_v2_resume_chunks.py
  # Resume:   python3 -u scripts/rebuild_chroma_v2_resume_chunks.py --resume
"""

import argparse
import json
import subprocess
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
SNAPSHOT_PATH = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2" / "v1_chunks_snapshot.json"
ERROR_LOG = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2" / "resume_chunks_errors.log"

BATCH_SIZE = 2                 # tiny to bound damage per hang
MAX_INPUT_CHARS = 7500         # ~2000 tokens; well under llama-server -c 4096
EMBED_MODEL = "qwen3-embedding:8b"
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "
BATCH_TIMEOUT_SEC = 60         # kill+retry any batch that stalls
PROGRESS_EVERY = 50
CHUNKS_COLLECTION = "chunks_v2"
OLLAMA_URL = "http://localhost:11434/api/embed"


def _snapshot_v1_via_subprocess():
    backups = sorted(REPO_ROOT.glob(".chroma_v1_backup_*"))
    if not backups:
        print("[fatal] no .chroma_v1_backup_* found")
        sys.exit(1)
    backup = backups[-1]
    print(f"[snapshot] using backup: {backup.name}", flush=True)

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT_PATH.exists() and SNAPSHOT_PATH.stat().st_size > 100:
        print(f"[snapshot] reusing existing {SNAPSHOT_PATH}", flush=True)
        return

    helper = (
        "import chromadb, json, sys, os\n"
        f"os.chdir({str(REPO_ROOT)!r})\n"
        f"client = chromadb.PersistentClient(path={str(backup)!r})\n"
        "col = client.get_collection('chunks')\n"
        "got = col.get(include=['documents', 'metadatas'])\n"
        f"json.dump({{'ids': got['ids'], 'docs': got['documents'], 'metas': got['metadatas']}}, open({str(SNAPSHOT_PATH)!r}, 'w'))\n"
        "print(f'[snapshot] wrote {len(got[\"ids\"])} chunks')"
    )
    result = subprocess.run([sys.executable, "-c", helper], capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print("[fatal] snapshot subprocess failed")
        sys.exit(1)


def _truncate(text: str) -> str:
    """Cap input to MAX_INPUT_CHARS. Truncate at nearest whitespace when possible."""
    if not text:
        return " "  # empty inputs may cause the embed server to hang
    if len(text) <= MAX_INPUT_CHARS:
        return text
    cut = text[:MAX_INPUT_CHARS]
    space = cut.rfind(" ")
    if space > MAX_INPUT_CHARS - 200:
        cut = cut[:space]
    return cut


def _embed_http(texts, timeout=BATCH_TIMEOUT_SEC):
    """Direct POST to Ollama /api/embed with a bounded timeout.

    Returns list[list[float]] or raises requests.Timeout / requests.RequestException.
    """
    prefixed = [QWEN3_PREFIX + _truncate(t) for t in texts]
    resp = requests.post(
        OLLAMA_URL,
        json={"model": EMBED_MODEL, "input": prefixed, "keep_alive": "10m"},
        timeout=timeout,
    )
    resp.raise_for_status()
    j = resp.json()
    return j["embeddings"]


def _embed_with_fallback(texts):
    """Try batch first. On timeout/error, fall back to per-item. Returns embeddings
    aligned to input length; None slots indicate hard failures."""
    try:
        return _embed_http(texts)
    except (requests.Timeout, requests.RequestException) as e:
        with ERROR_LOG.open("a") as f:
            f.write(f"batch fail ({len(texts)}): {e}\n")
        # Fall back to per-item.
        out = []
        for t in texts:
            try:
                out.append(_embed_http([t])[0])
            except (requests.Timeout, requests.RequestException) as e2:
                with ERROR_LOG.open("a") as f:
                    f.write(f"  item fail (len={len(t)}): {e2}\n")
                out.append(None)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true",
                    help="keep existing chunks_v2 rows and only embed the missing ids")
    args = ap.parse_args()

    print("=" * 72, flush=True)
    mode = "RESUME (skip already-embedded)" if args.resume else "FRESH (drop + rebuild)"
    print(f"Scribe: chunks_v2 build — mode: {mode}", flush=True)
    print("=" * 72, flush=True)

    _snapshot_v1_via_subprocess()
    with SNAPSHOT_PATH.open("r") as f:
        snap = json.load(f)
    ids = snap["ids"]
    docs = snap["docs"]
    metas = snap["metas"]
    total = len(ids)
    print(f"[chunks] snapshot has {total} chunks", flush=True)

    # Sanity ping Ollama before starting the loop.
    try:
        ping = _embed_http(["health check"], timeout=15)
        print(f"[chunks] ollama warm-up OK (dim={len(ping[0])})", flush=True)
    except Exception as e:
        print(f"[fatal] ollama warm-up failed: {e}")
        print("        run:  ollama serve   (in a separate terminal)")
        sys.exit(1)

    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if args.resume:
        try:
            coll = client.get_collection(CHUNKS_COLLECTION)
            existing = set(coll.get(include=[])["ids"])
            print(f"[chunks] resume: {len(existing)} existing rows in {CHUNKS_COLLECTION}", flush=True)
        except Exception:
            print(f"[chunks] resume: {CHUNKS_COLLECTION} not found, creating fresh", flush=True)
            coll = client.create_collection(
                name=CHUNKS_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            existing = set()
        # Filter out already-embedded ids from work list.
        keep = [i for i, cid in enumerate(ids) if cid not in existing]
        ids = [ids[i] for i in keep]
        docs = [docs[i] for i in keep]
        metas = [metas[i] for i in keep]
        remaining = len(ids)
        print(f"[chunks] resume: {remaining} chunks remaining ({total - remaining} already done)", flush=True)
        total = remaining
        if total == 0:
            print("[chunks] nothing to do; chunks_v2 already complete.", flush=True)
            print(f"[final] chunks_v2: {coll.count()} rows / {len(snap['ids'])} target")
            return
    else:
        try:
            client.delete_collection(CHUNKS_COLLECTION)
            print(f"[chunks] dropped partial {CHUNKS_COLLECTION}", flush=True)
        except Exception:
            pass
        coll = client.create_collection(
            name=CHUNKS_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"[chunks] created fresh {CHUNKS_COLLECTION}", flush=True)

    start = time.time()
    written = 0
    skipped = 0

    for start_i in range(0, total, BATCH_SIZE):
        end_i = min(start_i + BATCH_SIZE, total)
        batch_ids = ids[start_i:end_i]
        batch_docs = docs[start_i:end_i]
        batch_metas = [dict(m) if m else {} for m in metas[start_i:end_i]]

        emb = _embed_with_fallback(batch_docs)

        keep_ids, keep_docs, keep_metas, keep_emb = [], [], [], []
        for i, e in enumerate(emb):
            if e is not None:
                keep_ids.append(batch_ids[i])
                keep_docs.append(batch_docs[i])
                keep_metas.append(batch_metas[i])
                keep_emb.append(e)
            else:
                skipped += 1

        if keep_ids:
            try:
                coll.add(ids=keep_ids, documents=keep_docs, metadatas=keep_metas, embeddings=keep_emb)
                written += len(keep_ids)
            except Exception as e:
                with ERROR_LOG.open("a") as f:
                    f.write(f"chroma add fail (batch {start_i}): {e}\n")

        seen = end_i
        if seen % PROGRESS_EVERY == 0 or seen == total:
            elapsed = time.time() - start
            rate = seen / elapsed if elapsed > 0 else 0.0
            eta = (total - seen) / rate if rate > 0 else 0
            print(f"[chunks_v2] {seen}/{total} written={written} skipped={skipped} "
                  f"({rate:.1f}/s, ETA {eta:.0f}s)", flush=True)

    final = coll.count()
    print(flush=True)
    print("=" * 72, flush=True)
    print(f"chunks_v2: {final} rows written, {skipped} skipped. Facts: 6055.")
    print(f"Total time: {(time.time()-start)/60:.1f} min.")
    if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0:
        print(f"[note] some errors in {ERROR_LOG}")
    print("=" * 72, flush=True)


if __name__ == "__main__":
    main()
