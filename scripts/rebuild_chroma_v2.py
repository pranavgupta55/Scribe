#!/usr/bin/env python3
"""
Rebuild Scribe's ChromaDB in the v2 schema aligned with graph_v2.json.

WHAT THIS DOES
--------------
Scribe has two parallel data systems today:
  * v1  — process.py + .chroma/ with collections "facts" and "chunks",
          embedded via nomic-embed-text (768-d).
  * v2  — .scribe-skills/ phase3a/3b pipeline emitting
          graph/graph_v2.json (5477 nodes, 19154 links) and
          .scribe-skills/phase3b/merged_claims.jsonl (6055 merged claims),
          embedded via qwen3-embedding:8b (4096-d).

This script wipes the v1 .chroma/ (renamed as a backup, never deleted) and
rebuilds a fresh .chroma/ with two v2 collections:

  * facts_v2   — one entry per merged claim (id="claim:{claim_id}"),
                 text embedded with qwen3-embedding:8b. Metadata carries
                 topic, level (from graph_v2), source_count, and the
                 full attribution_list serialized as JSON.
  * chunks_v2  — chunks carried over from v1 (same ids, same text, same
                 metadata) but re-embedded with qwen3-embedding:8b so the
                 vector space matches facts_v2 for downstream cosine joins.

Downstream retrieval can then directly join hits in facts_v2 back to
graph_v2.json edges via the shared "claim:{id}" identifier.

USAGE
-----
    python3 scripts/rebuild_chroma_v2.py [--force]

--force skips the interactive confirmation prompt.

WALL TIME
---------
Roughly 30-45 minutes on an M-series Mac with qwen3-embedding:8b already
loaded in Ollama. Dominated by embedding ~6055 claims + ~3551 chunks.

WHY THIS SCRIPT IS IDEMPOTENT
-----------------------------
It renames (not deletes) the existing .chroma/ to
.chroma_v1_backup_YYYYMMDD_HHMMSS/ before rebuilding. Rerunning is safe:
every run produces a fresh timestamped backup. Nothing is destructive.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = REPO_ROOT / ".chroma"
MERGED_CLAIMS = REPO_ROOT / ".scribe-skills" / "phase3b" / "merged_claims.jsonl"
GRAPH_V2 = REPO_ROOT / "graph" / "graph_v2.json"
ERROR_LOG_DIR = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2"
ERROR_LOG = ERROR_LOG_DIR / "rebuild_errors.log"

EMBED_MODEL = "qwen3-embedding:8b"

# Matches .scribe-skills/scripts/phase3b_prepass.py so query-time embeddings
# live in the same vector distribution as the pre-passed claim embeddings.
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "

BATCH_SIZE = 16              # ollama embed batches — 8..16 is the sweet spot
MAX_TEXT_CHARS = 30000       # truncate longer bodies to protect the model
PROGRESS_EVERY = 100         # print a progress line every N items

FACTS_COLLECTION = "facts_v2"
CHUNKS_COLLECTION = "chunks_v2"

# ---------------------------------------------------------------------------
# Embedding backend
# ---------------------------------------------------------------------------


def _detect_embed_backend():
    """Return a function `embed_batch(list_of_str) -> list_of_list_of_float`.

    Prefers the `ollama` Python client if importable; falls back to plain
    HTTP POST to localhost:11434/api/embed (which is what the client uses
    under the hood anyway).
    """
    try:
        import ollama  # type: ignore

        def _embed_ollama(texts):
            resp = ollama.embed(model=EMBED_MODEL, input=texts)
            return list(resp["embeddings"])

        return _embed_ollama
    except ImportError:
        pass

    import requests  # deferred so we only require it if needed

    def _embed_http(texts):
        r = requests.post(
            "http://localhost:11434/api/embed",
            json={"model": EMBED_MODEL, "input": texts},
            timeout=600,
        )
        r.raise_for_status()
        return r.json()["embeddings"]

    return _embed_http


def _prep_text(t: str) -> str:
    """Truncate + prefix a raw text for embedding."""
    if not isinstance(t, str):
        t = "" if t is None else str(t)
    if len(t) > MAX_TEXT_CHARS:
        print(f"[warn] text length {len(t)} > {MAX_TEXT_CHARS}, truncating")
        t = t[:MAX_TEXT_CHARS]
    return QWEN3_PREFIX + t


def _log_error(msg: str) -> None:
    ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ERROR_LOG.open("a") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------


def check_prereqs() -> None:
    # ollama model
    try:
        import ollama  # type: ignore

        try:
            models_resp = ollama.list()
            models = [
                (m.get("model") or m.get("name") or "")
                for m in (models_resp.get("models") or [])
            ]
        except Exception:
            models = []
        have_model = any(EMBED_MODEL in m for m in models)
    except ImportError:
        # No ollama client — best-effort HTTP check
        try:
            import requests

            r = requests.get("http://localhost:11434/api/tags", timeout=10)
            r.raise_for_status()
            models = [m.get("model", "") for m in r.json().get("models", [])]
            have_model = any(EMBED_MODEL in m for m in models)
        except Exception:
            have_model = False

    if not have_model:
        print(
            f"[fatal] ollama model '{EMBED_MODEL}' not found.\n"
            f"        Run: ollama pull {EMBED_MODEL}"
        )
        sys.exit(1)

    if not MERGED_CLAIMS.is_file():
        print(f"[fatal] missing {MERGED_CLAIMS}")
        sys.exit(1)
    if not GRAPH_V2.is_file():
        print(f"[fatal] missing {GRAPH_V2}")
        sys.exit(1)
    if not CHROMA_DIR.is_dir():
        print(
            f"[fatal] {CHROMA_DIR} not found — run process.py first to build "
            "the v1 baseline."
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# v1 snapshot
# ---------------------------------------------------------------------------


def snapshot_v1_chunks():
    """Pull all chunks from the existing v1 chroma into memory before wipe.

    ChromaDB 1.5.9 uses a process-level SharedSystemClient that caches SQLite
    handles even after `del client`. If we opened v1 in this process, the
    stale handle is reused when we later open v2 on the (renamed) same path
    and the second open comes up read-only. Fix: run the snapshot in a
    subprocess so its ChromaDB state fully exits before we wipe/rebuild.
    """
    snapshot_path = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2" / "v1_chunks_snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    # Invoke self as a subprocess with --snapshot-only. Subprocess writes JSON
    # to snapshot_path and exits, releasing all SQLite handles.
    result = subprocess.run(
        [sys.executable, __file__, "--snapshot-only", "--snapshot-out", str(snapshot_path)],
        capture_output=True,
        text=True,
    )
    # Forward child stdout so [snapshot] progress lines appear in the parent log.
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.returncode != 0:
        print(f"[fatal] snapshot subprocess failed (rc={result.returncode})")
        if result.stderr:
            sys.stderr.write(result.stderr)
        sys.exit(1)

    with snapshot_path.open("r") as f:
        snap = json.load(f)

    return snap["ids"], snap["docs"], snap["metas"], snap["total"]


def _snapshot_only(out_path: Path):
    """Subprocess entrypoint: dump v1 chunks to JSON and exit."""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        chunks = client.get_collection("chunks")
    except Exception as e:
        print(f"[fatal] can't open v1 chunks collection: {e}")
        sys.exit(1)

    total = chunks.count()
    print(f"[snapshot] v1 chunks count: {total}")
    got = chunks.get(include=["documents", "metadatas"])
    ids = got["ids"]
    docs = got["documents"]
    metas = got["metadatas"]
    print(f"[snapshot] pulled {len(ids)} chunks from v1")

    with out_path.open("w") as f:
        json.dump({"ids": ids, "docs": docs, "metas": metas, "total": total}, f)
    print(f"[snapshot] wrote {out_path}")


# ---------------------------------------------------------------------------
# Wipe (rename to timestamped backup)
# ---------------------------------------------------------------------------


def wipe_chroma() -> Path:
    """Rename .chroma/ -> .chroma_v1_backup_<ts>/. Rename (not delete) so a
    user can always recover the old vectors."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = REPO_ROOT / f".chroma_v1_backup_{ts}"
    if backup.exists():
        # Extremely unlikely (same-second collision); disambiguate.
        backup = REPO_ROOT / f".chroma_v1_backup_{ts}_{os.getpid()}"
    shutil.move(str(CHROMA_DIR), str(backup))
    CHROMA_DIR.mkdir(parents=True, exist_ok=False)
    print(f"[wipe] moved old .chroma/ -> {backup.name}")
    return backup


# ---------------------------------------------------------------------------
# Batched embed + add with retry / skip
# ---------------------------------------------------------------------------


def _embed_with_retry(embed_fn, texts):
    """Try to embed `texts` as one batch. On failure, fall back to size=1 to
    isolate the bad item. Returns a list where failed slots are None."""
    try:
        prepped = [_prep_text(t) for t in texts]
        return embed_fn(prepped)
    except Exception as e:
        _log_error(f"batch of {len(texts)} failed: {e}")
        print(f"[warn] batch of {len(texts)} failed ({e}); retrying one-by-one")

    out = []
    for i, t in enumerate(texts):
        try:
            prepped = [_prep_text(t)]
            e1 = embed_fn(prepped)
            out.append(e1[0])
        except Exception as e:
            _log_error(f"single-item embed failed (offset {i}): {e}")
            print(f"[warn] skipping item at batch-offset {i}: {e}")
            out.append(None)
    return out


def _flush_batch(collection, embed_fn, ids, docs, metas):
    """Embed and add one batch. Skips items whose embedding failed."""
    embeddings = _embed_with_retry(embed_fn, docs)
    keep_ids, keep_docs, keep_metas, keep_embs = [], [], [], []
    for i, e in enumerate(embeddings):
        if e is None:
            _log_error(f"[{collection.name}] dropping id={ids[i]} (embed failed)")
            continue
        keep_ids.append(ids[i])
        keep_docs.append(docs[i])
        keep_metas.append(metas[i])
        keep_embs.append(e)
    if keep_ids:
        collection.add(
            ids=keep_ids,
            documents=keep_docs,
            embeddings=keep_embs,
            metadatas=keep_metas,
        )
    return len(keep_ids)


# ---------------------------------------------------------------------------
# facts_v2
# ---------------------------------------------------------------------------


def build_facts_v2(chroma_client, embed_fn, level_by_claim):
    coll = chroma_client.create_collection(
        name=FACTS_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    total_written = 0
    total_seen = 0
    start = time.time()
    batch_ids, batch_docs, batch_metas = [], [], []

    with MERGED_CLAIMS.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                _log_error(f"[facts_v2] bad JSON line: {e}")
                continue

            claim_id = row.get("claim_id")
            text = row.get("text") or ""
            if claim_id is None or not text:
                _log_error(f"[facts_v2] skipping row missing id/text: {row.get('claim_id')}")
                continue

            cid = f"claim:{claim_id}"
            attributions = row.get("attribution_list") or []
            meta = {
                "topic": row.get("topic") or "",
                "level": level_by_claim.get(cid, "L2"),
                "source_count": len(attributions),
                # attribution_list is a list of dicts and Chroma metadata only
                # accepts scalar values — JSON-serialize so downstream can parse.
                "attribution_json": json.dumps(attributions, ensure_ascii=False),
            }

            batch_ids.append(cid)
            batch_docs.append(text)
            batch_metas.append(meta)
            total_seen += 1

            if len(batch_ids) >= BATCH_SIZE:
                total_written += _flush_batch(
                    coll, embed_fn, batch_ids, batch_docs, batch_metas
                )
                batch_ids, batch_docs, batch_metas = [], [], []

            if total_seen % PROGRESS_EVERY == 0:
                elapsed = time.time() - start
                rate = total_seen / elapsed if elapsed > 0 else 0.0
                eta = (6055 - total_seen) / rate if rate > 0 else 0
                print(
                    f"[facts_v2] {total_seen}/6055 written={total_written} "
                    f"({rate:.1f}/s, ETA {eta:.0f}s)"
                )

    # Flush tail
    if batch_ids:
        total_written += _flush_batch(
            coll, embed_fn, batch_ids, batch_docs, batch_metas
        )

    dur = time.time() - start
    print(
        f"[facts_v2] done: written={total_written} of seen={total_seen} "
        f"in {dur:.1f}s"
    )
    return total_written, dur


# ---------------------------------------------------------------------------
# chunks_v2
# ---------------------------------------------------------------------------


def build_chunks_v2(chroma_client, embed_fn, ids, docs, metas):
    coll = chroma_client.create_collection(
        name=CHUNKS_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    total_target = len(ids)
    total_written = 0
    start = time.time()

    for start_i in range(0, total_target, BATCH_SIZE):
        end_i = min(start_i + BATCH_SIZE, total_target)
        batch_ids = ids[start_i:end_i]
        batch_docs = docs[start_i:end_i]
        # Carry v1 metadata forward verbatim. We do NOT compute claim_ids[] here;
        # that's deferred to runtime via cosine lookup against facts_v2.
        batch_metas = [dict(m) if m else {} for m in metas[start_i:end_i]]

        total_written += _flush_batch(
            coll, embed_fn, batch_ids, batch_docs, batch_metas
        )

        seen = end_i
        if seen % PROGRESS_EVERY == 0 or seen == total_target:
            elapsed = time.time() - start
            rate = seen / elapsed if elapsed > 0 else 0.0
            eta = (total_target - seen) / rate if rate > 0 else 0
            print(
                f"[chunks_v2] {seen}/{total_target} written={total_written} "
                f"({rate:.1f}/s, ETA {eta:.0f}s)"
            )

    dur = time.time() - start
    print(
        f"[chunks_v2] done: written={total_written} of seen={total_target} "
        f"in {dur:.1f}s"
    )
    return total_written, dur


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the interactive confirmation prompt",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: subprocess entrypoint for v1 snapshot
    )
    parser.add_argument(
        "--snapshot-out",
        type=str,
        default="",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.snapshot_only:
        _snapshot_only(Path(args.snapshot_out))
        return

    print("=" * 72)
    print("Scribe: rebuild ChromaDB in v2 schema (facts_v2 + chunks_v2)")
    print("=" * 72)

    check_prereqs()
    embed_fn = _detect_embed_backend()

    # 1. Snapshot v1 chunks BEFORE wipe.
    v1_ids, v1_docs, v1_metas, v1_count = snapshot_v1_chunks()

    # 2. Build claim_id -> level map.
    print(f"[graph] loading {GRAPH_V2.name} ...")
    with GRAPH_V2.open("r") as f:
        g = json.load(f)
    level_by_claim = {
        n["id"]: n.get("level", "L2")
        for n in g.get("nodes", [])
        if str(n.get("id", "")).startswith("claim:")
    }
    print(f"[graph] {len(level_by_claim)} claim nodes carry a level")

    # 3. Confirm.
    if not args.force:
        ans = input(
            "\nAbout to wipe .chroma/ and rebuild. "
            "Old data will be renamed to .chroma_v1_backup_<ts>/.\n"
            "Continue? [y/N] "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print("aborted.")
            sys.exit(0)

    # 4. Wipe.
    backup_path = wipe_chroma()

    # 5. Fresh client on the empty .chroma/.
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # 6. Build facts_v2.
    t0 = time.time()
    n_facts, facts_dur = build_facts_v2(client, embed_fn, level_by_claim)

    # 7. Build chunks_v2 from the in-memory snapshot.
    n_chunks, chunks_dur = build_chunks_v2(client, embed_fn, v1_ids, v1_docs, v1_metas)

    # 8. Self-check.
    facts_final = client.get_collection(FACTS_COLLECTION).count()
    chunks_final = client.get_collection(CHUNKS_COLLECTION).count()
    print(f"\n[self-check] facts_v2 count: {facts_final}  (wrote {n_facts})")
    print(f"[self-check] chunks_v2 count: {chunks_final}  (wrote {n_chunks}, v1 had {v1_count})")

    total_dur = time.time() - t0
    total_items = n_facts + n_chunks
    embed_rate = total_items / total_dur if total_dur > 0 else 0

    print()
    print("=" * 72)
    print(
        f"Wrote {n_facts} facts, {n_chunks} chunks. "
        f"Original v1 at {backup_path.name}/. Ready."
    )
    print(f"Total time: {total_dur / 60:.1f} min. Embed avg: {embed_rate:.1f}/s.")
    if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0:
        print(f"[note] some errors logged to {ERROR_LOG}")
    print("=" * 72)


if __name__ == "__main__":
    main()
