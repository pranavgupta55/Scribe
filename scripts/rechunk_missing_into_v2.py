#!/usr/bin/env python3
"""Repair: chunk-index sources that are in sources.json + graph but absent from chunks_v2.

Background: the Jun-21 Rory batch (and any other `"via"`-tagged sources) entered
sources.json via the graph pipeline (phase3a_build_sources.py), NOT via process.py,
so they were never sectioned/chunked into ChromaDB. Neither rebuild_chroma_v2.py nor
its --resume variant can recover them (both source chunks from the v1 backup, which
also lacks them). Their transcripts DO exist on disk, so we re-chunk from raw text.

Chunks are fixed ~word-window slices (no LLM sectioning — section bodies are
unrecoverable), embedded with qwen3-embedding:8b to match the chunks_v2 vector space,
and upserted with the same id scheme (`{name}__s{i}`) and metadata schema as v1 chunks.

Usage:
    python3 -u scripts/rechunk_missing_into_v2.py           # dry-run (list only)
    python3 -u scripts/rechunk_missing_into_v2.py --write   # embed + upsert
"""
import argparse, json, os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHROMA = REPO / ".chroma"
TRANSCRIPTS = REPO / "transcripts"
SOURCES = REPO / "knowledge" / "sources.json"

EMBED_MODEL   = "qwen3-embedding:8b"
QWEN3_PREFIX  = "Instruct: Retrieve semantically similar text.\nQuery: "
MAX_TEXT_CHARS = 7500          # keep well under llama-server -c 4096 to avoid silent hangs
WINDOW_WORDS   = 1100
OVERLAP_WORDS  = 150


def prettify(name: str) -> str:
    base = name[:-4] if name.endswith(".txt") else name
    # strip trailing 11-char youtube id
    parts = base.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 11:
        base = parts[0]
    return base.replace("_", " ").strip().title()


def windows(text: str):
    words = text.split()
    if not words:
        return
    step = WINDOW_WORDS - OVERLAP_WORDS
    for i in range(0, len(words), step):
        yield " ".join(words[i:i + WINDOW_WORDS])
        if i + WINDOW_WORDS >= len(words):
            break


def embed_one(text: str):
    import requests
    body = QWEN3_PREFIX + text[:MAX_TEXT_CHARS]
    r = requests.post("http://localhost:11434/api/embed",
                      json={"model": EMBED_MODEL, "input": [body]}, timeout=120)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="actually embed + upsert (default: dry-run)")
    args = ap.parse_args()

    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA))
    chunks = client.get_collection("chunks_v2")
    indexed = set(m.get("source") for m in chunks.get(include=["metadatas"])["metadatas"]
                  if m and m.get("source"))
    srcs = json.loads(SOURCES.read_text())
    missing = [n for n in srcs if n not in indexed and (TRANSCRIPTS / n).exists()]
    missing.sort()

    print(f"chunks_v2 indexed sources: {len(indexed)} | sources.json: {len(srcs)} | "
          f"missing w/ transcript on disk: {len(missing)}")
    no_txt = [n for n in srcs if n not in indexed and not (TRANSCRIPTS / n).exists()]
    if no_txt:
        print(f"  ! {len(no_txt)} missing sources have NO transcript on disk (cannot recover): {no_txt[:3]}")

    if not args.write:
        print("\nDRY RUN — pass --write to embed + upsert. First 5 targets:")
        for n in missing[:5]:
            wc = len((TRANSCRIPTS / n).read_text(encoding="utf-8").split())
            print(f"  {n}  (~{wc} words, ~{max(1, wc // (WINDOW_WORDS - OVERLAP_WORDS))} chunks)")
        return

    total_chunks = 0
    t0 = time.time()
    for si, name in enumerate(missing, 1):
        text = (TRANSCRIPTS / name).read_text(encoding="utf-8").strip()
        pretty = prettify(name)
        wins = list(windows(text))
        ids, docs, embs, metas = [], [], [], []
        for i, w in enumerate(wins):
            try:
                emb = embed_one(w)
            except Exception as e:
                print(f"  ! embed fail {name} s{i}: {str(e)[:80]} — skipping window")
                continue
            ids.append(f"{name}__s{i}")
            docs.append(w)
            embs.append(emb)
            metas.append({
                "source": name,
                "section_idx": i,
                "section_title": f"{pretty} — part {i + 1}",
                "premise": "",
                "conclusion": "",
            })
        if ids:
            chunks.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
            total_chunks += len(ids)
        print(f"  [{si}/{len(missing)}] {name[:55]} — {len(ids)} chunks "
              f"({total_chunks} total, {time.time() - t0:.0f}s)")

    final = set(m.get("source") for m in chunks.get(include=["metadatas"])["metadatas"]
                if m and m.get("source"))
    print(f"\nDONE — wrote {total_chunks} chunks. chunks_v2 now covers {len(final)} sources.")


if __name__ == "__main__":
    main()
