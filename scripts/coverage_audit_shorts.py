#!/usr/bin/env python3
"""Cosine-similarity coverage audit for unprocessed Scribe transcripts.

For each unprocessed short (query set), compute max cosine similarity against
every already-processed long-form transcript (reference set). Emit a matplotlib
histogram so the user can pick a cutoff — transcripts above cutoff are dropped
as near-duplicates of already-processed longs.

Also emits a second histogram for unprocessed FULL-LENGTH transcripts (non-clip)
as a sanity check for accidental duplicates.

Inputs:
  transcripts/*.txt (+ optional transcripts/*.meta.json)
  knowledge/sources.json

Outputs:
  .forge_scratch/scribe_rag_v2/coverage_audit.json         — per-short details
  .forge_scratch/scribe_rag_v2/coverage_audit_fulllen.json — per-fulllen details
  .forge_scratch/scribe_rag_v2/coverage_histogram.png      — shorts histogram
  .forge_scratch/scribe_rag_v2/coverage_histogram_fulllen.png — fulllen histogram
  .forge_scratch/scribe_rag_v2/coverage_shorts_embeddings.npy
  .forge_scratch/scribe_rag_v2/coverage_longs_embeddings.npy
  .forge_scratch/scribe_rag_v2/coverage_fulllen_embeddings.npy

Usage:
  python3 -u scripts/coverage_audit_shorts.py [--rebuild-embeddings]
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import numpy as np
    import requests
except ImportError as e:
    print(f"[fatal] missing dep: {e}. pip install numpy requests matplotlib")
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent.resolve()
TRANSCRIPTS = REPO_ROOT / "transcripts"
SOURCES_JSON = REPO_ROOT / "knowledge" / "sources.json"
SCRATCH = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2"

SHORTS_EMB = SCRATCH / "coverage_shorts_embeddings.npy"
SHORTS_NAMES = SCRATCH / "coverage_shorts_names.json"
LONGS_EMB = SCRATCH / "coverage_longs_embeddings.npy"
LONGS_NAMES = SCRATCH / "coverage_longs_names.json"
FULLLEN_EMB = SCRATCH / "coverage_fulllen_embeddings.npy"
FULLLEN_NAMES = SCRATCH / "coverage_fulllen_names.json"

AUDIT_JSON = SCRATCH / "coverage_audit.json"
AUDIT_FULLLEN_JSON = SCRATCH / "coverage_audit_fulllen.json"
HIST_PNG = SCRATCH / "coverage_histogram.png"
HIST_FULLLEN_PNG = SCRATCH / "coverage_histogram_fulllen.png"

EMBED_MODEL = "qwen3-embedding:8b"
OLLAMA_URL = "http://localhost:11434/api/embed"
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "

TRUNCATE_CHARS = 5000
BATCH_SIZE = 8
BATCH_TIMEOUT_SEC = 120
PROGRESS_EVERY = 50


def _embed_http(texts, timeout=BATCH_TIMEOUT_SEC):
    prefixed = [QWEN3_PREFIX + t for t in texts]
    resp = requests.post(
        OLLAMA_URL,
        json={"model": EMBED_MODEL, "input": prefixed, "keep_alive": "10m"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["embeddings"]


def _embed_batched(texts, label):
    if not texts:
        return np.zeros((0, 4096), dtype=np.float32)
    out = []
    total = len(texts)
    start = time.time()
    for i in range(0, total, BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        for attempt in range(3):
            try:
                embs = _embed_http(batch)
                out.extend(embs)
                break
            except requests.exceptions.Timeout:
                if attempt == 2:
                    print(f"[warn] batch {i}-{i+len(batch)} timed out 3x; retrying per-item")
                    for t in batch:
                        try:
                            out.extend(_embed_http([t]))
                        except Exception as e:
                            print(f"  [skip] {e}")
                            out.append([0.0] * 4096)
                    break
                time.sleep(1 + attempt)
            except Exception as e:
                if attempt == 2:
                    print(f"[warn] batch {i} failed: {e}; padding zero vectors")
                    out.extend([[0.0] * 4096] * len(batch))
                    break
                time.sleep(1 + attempt)
        if (i + BATCH_SIZE) % PROGRESS_EVERY < BATCH_SIZE:
            done = i + len(batch)
            elapsed = time.time() - start
            rate = done / max(elapsed, 0.01)
            eta = (total - done) / max(rate, 0.01)
            print(f"  [{label}] {done}/{total} ({rate:.1f}/s, ETA {eta:.0f}s)")
    return np.array(out, dtype=np.float32)


def _load_text(p: Path) -> str:
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return raw[:TRUNCATE_CHARS].strip()


def _is_short(txt_path: Path) -> bool:
    """Filename prefix `moremozi_` OR meta.content_category == 'moremozi'."""
    if txt_path.name.startswith("moremozi_"):
        return True
    meta = txt_path.with_suffix(".meta.json")
    if meta.exists():
        try:
            m = json.load(open(meta))
            if m.get("content_category") == "moremozi":
                return True
        except Exception:
            pass
    return False


def _healthcheck_ollama():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        if EMBED_MODEL not in models:
            print(f"[fatal] {EMBED_MODEL} not pulled. Run: ollama pull {EMBED_MODEL}")
            sys.exit(1)
        # warm
        _embed_http(["warmup"])
    except Exception as e:
        print(f"[fatal] Ollama not reachable: {e}. Run: ollama serve")
        sys.exit(1)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity matrix: (Na, D) @ (Nb, D) → (Na, Nb)."""
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a_norm @ b_norm.T


def _load_or_embed(emb_path: Path, names_path: Path, items, label: str, force: bool):
    """items: list[(name, text)]. Returns (names, embeddings ndarray)."""
    if not force and emb_path.exists() and names_path.exists():
        names = json.load(open(names_path))
        embs = np.load(emb_path)
        if len(names) == embs.shape[0] and set(names) == {n for n, _ in items}:
            print(f"[cache] {label}: reusing {len(names)} embeddings from disk")
            # reorder to match items input
            idx = {n: i for i, n in enumerate(names)}
            reordered = np.stack([embs[idx[n]] for n, _ in items])
            return [n for n, _ in items], reordered
        print(f"[cache] {label}: stale cache (names/count changed), re-embedding")
    names = [n for n, _ in items]
    texts = [t for _, t in items]
    embs = _embed_batched(texts, label)
    np.save(emb_path, embs)
    json.dump(names, open(names_path, "w"))
    print(f"[cache] {label}: saved {len(names)} embeddings → {emb_path}")
    return names, embs


def _plot_histogram(sims_max, out_png, title, xlabel="max cosine similarity"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(sims_max, bins=40, range=(0.0, 1.0), edgecolor="black", alpha=0.75)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("count")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    for t in (0.6, 0.7, 0.8, 0.9):
        ax.axvline(t, color="red", alpha=0.4, linestyle="--", linewidth=1)
        ax.text(t + 0.005, ax.get_ylim()[1] * 0.9, f"{t}", color="red", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def _bucket_report(sims_max):
    n = len(sims_max)
    if n == 0:
        return {"total": 0}
    return {
        "total": n,
        "mean": float(np.mean(sims_max)),
        "median": float(np.median(sims_max)),
        "ge_0.9": int(np.sum(sims_max >= 0.9)),
        "ge_0.8": int(np.sum(sims_max >= 0.8)),
        "ge_0.7": int(np.sum(sims_max >= 0.7)),
        "ge_0.6": int(np.sum(sims_max >= 0.6)),
        "lt_0.6": int(np.sum(sims_max < 0.6)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-embeddings", action="store_true",
                    help="Force re-embed even if NPY caches exist")
    args = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)

    _healthcheck_ollama()

    print("[load] reading sources.json + walking transcripts/")
    sources = json.load(open(SOURCES_JSON))
    processed_names = set(sources.keys())

    all_txts = sorted(TRANSCRIPTS.glob("*.txt"))
    print(f"  {len(all_txts)} transcript files on disk")
    print(f"  {len(processed_names)} processed sources (from sources.json)")

    # Classify
    shorts_unproc, fulllen_unproc, longs_proc = [], [], []
    empty = 0

    for p in all_txts:
        # sources.json keys — check both filename and stem
        is_proc = p.name in processed_names or p.stem in processed_names
        text = _load_text(p)
        if not text:
            empty += 1
            continue
        item = (p.name, text)
        if is_proc:
            longs_proc.append(item)
        else:
            if _is_short(p):
                shorts_unproc.append(item)
            else:
                fulllen_unproc.append(item)

    print(f"  → shorts unprocessed:    {len(shorts_unproc)}")
    print(f"  → fulllen unprocessed:   {len(fulllen_unproc)}")
    print(f"  → longs processed (ref): {len(longs_proc)}")
    print(f"  → empty/skipped:         {empty}")

    if not longs_proc:
        print("[fatal] no processed longs — nothing to compare against")
        sys.exit(1)

    # Embed reference set once
    print("\n[embed] longs_processed (reference set)")
    longs_names, longs_embs = _load_or_embed(
        LONGS_EMB, LONGS_NAMES, longs_proc, "longs", args.rebuild_embeddings
    )

    audit_summary = {}

    # SHORTS audit
    if shorts_unproc:
        print("\n[embed] shorts_unprocessed (query set)")
        shorts_names, shorts_embs = _load_or_embed(
            SHORTS_EMB, SHORTS_NAMES, shorts_unproc, "shorts", args.rebuild_embeddings
        )

        print("\n[cosine] shorts @ longs.T")
        sims = _cosine_sim(shorts_embs, longs_embs)
        max_idx = np.argmax(sims, axis=1)
        max_sim = np.max(sims, axis=1)

        details = [
            {
                "short": shorts_names[i],
                "max_sim": float(max_sim[i]),
                "best_long": longs_names[int(max_idx[i])],
            }
            for i in range(len(shorts_names))
        ]
        details.sort(key=lambda d: -d["max_sim"])
        json.dump(details, open(AUDIT_JSON, "w"), indent=2)
        print(f"  wrote {AUDIT_JSON}")

        _plot_histogram(max_sim, HIST_PNG,
                        f"Shorts vs Longs — max cosine similarity (N={len(shorts_names)})")
        print(f"  wrote {HIST_PNG}")

        audit_summary["shorts"] = _bucket_report(max_sim)
    else:
        print("[skip] no unprocessed shorts")
        audit_summary["shorts"] = _bucket_report(np.array([]))

    # FULL-LENGTH audit (spot check for accidental dupes)
    if fulllen_unproc:
        print("\n[embed] fulllen_unprocessed (spot-check set)")
        fl_names, fl_embs = _load_or_embed(
            FULLLEN_EMB, FULLLEN_NAMES, fulllen_unproc, "fulllen", args.rebuild_embeddings
        )

        print("\n[cosine] fulllen @ longs.T")
        sims2 = _cosine_sim(fl_embs, longs_embs)
        max_idx2 = np.argmax(sims2, axis=1)
        max_sim2 = np.max(sims2, axis=1)

        details2 = [
            {
                "fulllen": fl_names[i],
                "max_sim": float(max_sim2[i]),
                "best_long": longs_names[int(max_idx2[i])],
            }
            for i in range(len(fl_names))
        ]
        details2.sort(key=lambda d: -d["max_sim"])
        json.dump(details2, open(AUDIT_FULLLEN_JSON, "w"), indent=2)
        print(f"  wrote {AUDIT_FULLLEN_JSON}")

        _plot_histogram(max_sim2, HIST_FULLLEN_PNG,
                        f"Full-length unprocessed vs Longs — max cosine similarity (N={len(fl_names)})")
        print(f"  wrote {HIST_FULLLEN_PNG}")

        audit_summary["fulllen"] = _bucket_report(max_sim2)
    else:
        print("[skip] no unprocessed full-length")
        audit_summary["fulllen"] = _bucket_report(np.array([]))

    print("\n[summary]")
    print(json.dumps(audit_summary, indent=2))
    print(f"\nHistograms:")
    print(f"  {HIST_PNG}")
    print(f"  {HIST_FULLLEN_PNG}")
    print("\nOpen the shorts histogram and pick a cutoff T.")
    print("Everything with max_sim >= T is treated as a duplicate and dropped.")


if __name__ == "__main__":
    main()
