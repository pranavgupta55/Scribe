#!/usr/bin/env python3
"""
Generate graph/graph.json from the knowledge base for the Scribe graph viewer.

Nodes:
  - Topics  (from knowledge/topics/*.md)  — coloured by dominant source
  - Sources (from knowledge/sources.json) — hub style (no plugin)

Edges:
  - Topic ↔ Source  : topic appears in that source
  - Topic ↔ Topic   : co-occurrence in the same source

PCA layout (optional):
  If ChromaDB is populated and Ollama is running, topic embeddings are
  projected to 2D via PCA to seed the initial force-layout positions.
  Skipped silently if unavailable.
"""

import json
import re
import sys
import numpy as np
from pathlib import Path

SCRIPT_DIR       = Path(__file__).parent.resolve()
KNOWLEDGE_DIR    = SCRIPT_DIR / "knowledge"
TOPICS_DIR       = KNOWLEDGE_DIR / "topics"
SOURCES_FILE     = KNOWLEDGE_DIR / "sources.json"
CONNECTIONS_FILE = KNOWLEDGE_DIR / "connections.json"
CHROMA_DIR       = SCRIPT_DIR / ".chroma"
GRAPH_DIR        = SCRIPT_DIR / "graph"
GRAPH_FILE       = GRAPH_DIR / "graph.json"

EMBED_MODEL = "nomic-embed-text"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def read_topic_md(path):
    """Return (headline, full_body) stripping H1 and Sources footer."""
    text = path.read_text(encoding="utf-8").strip()
    text = re.sub(r"^#[^\n]+\n+", "", text)             # remove H1
    text = re.sub(r"\n---\n.*$", "", text, flags=re.DOTALL).strip()  # remove footer
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    first = paras[0] if paras else ""
    first = re.sub(r"^>\s*", "", first)                 # strip leading blockquote marker
    return first, text


def pca_2d(X):
    """Minimal 2D PCA using numpy (no sklearn needed)."""
    X = X - X.mean(axis=0)
    cov = np.cov(X.T)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    W = vecs[:, order[:2]]
    return X @ W


def get_pca_positions(topic_slugs):
    """Returns {slug: [x, y]}. Silently returns {} if unavailable."""
    if not CHROMA_DIR.exists():
        return {}
    try:
        import chromadb, ollama
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        facts_col = client.get_collection("facts")
        if facts_col.count() == 0:
            return {}
        ollama.list()  # verify running
    except Exception:
        return {}

    import ollama
    embeddings, valid = [], []
    for s in topic_slugs:
        try:
            emb = ollama.embeddings(model=EMBED_MODEL, prompt=s.replace("_", " "))["embedding"]
            embeddings.append(emb)
            valid.append(s)
        except Exception:
            continue

    if len(embeddings) < 2:
        return {}

    arr = np.array(embeddings, dtype=float)
    coords = pca_2d(arr)
    span = (coords.max(axis=0) - coords.min(axis=0)).max() or 1
    coords = (coords - coords.mean(axis=0)) * (500.0 / span)

    return {s: [round(float(x), 1), round(float(y), 1)]
            for s, (x, y) in zip(valid, coords)}


def build_graph():
    if not SOURCES_FILE.exists():
        print("❌ knowledge/sources.json not found. Run updateDB.sh first.")
        sys.exit(1)

    sources    = json.loads(SOURCES_FILE.read_text())
    topic_files = sorted(TOPICS_DIR.glob("*.md")) if TOPICS_DIR.exists() else []

    if not topic_files and not sources:
        print("❌ Knowledge base is empty. Run updateDB.sh first.")
        sys.exit(1)

    print(f"Building graph: {len(topic_files)} topics · {len(sources)} sources")

    topic_slugs   = [tf.stem for tf in topic_files]
    topic_display = {s: s.replace("_", " ").title() for s in topic_slugs}

    # --- PCA layout ---
    print("Computing PCA layout...", end=" ", flush=True)
    pca_pos = get_pca_positions(topic_slugs)
    print(f"{'✓ ' + str(len(pca_pos)) + ' positions' if pca_pos else 'skipped (run updateDB.sh to populate)'}")

    # --- Source → topic slug map ---
    source_topic_slugs = {}          # src_file → set of slugs
    for src, meta in sources.items():
        source_topic_slugs[src] = {slug(t) for t in meta.get("topics", [])}

    # --- Dominant source per topic (deterministic: first alphabetically) ---
    topic_srcs = {}
    for src, slugs in source_topic_slugs.items():
        for s in slugs:
            topic_srcs.setdefault(s, []).append(src)
    dominant = {s: sorted(srcs)[0] for s, srcs in topic_srcs.items()}

    # ------------------------------------------------------------------ nodes
    nodes = []

    for tf in topic_files:
        s = tf.stem
        first_para, full = read_topic_md(tf)
        claim_count = sum(
            meta.get("claim_count", meta.get("fact_count", 0))
            for src, meta in sources.items()
            if s in {slug(t) for t in meta.get("topics", [])}
        )
        node = {
            "id":          topic_display[s],
            "plugin":      dominant.get(s),          # source filename → colour
            "version":     "1.0",
            "description": full if full else f"{claim_count} extracted claims",
        }
        if s in pca_pos:
            node["pos"] = pca_pos[s]
        nodes.append(node)

    for src, meta in sorted(sources.items()):
        topic_list = ", ".join(meta.get("topics", [])[:6])
        claim_n   = meta.get("claim_count", meta.get("fact_count", 0))
        section_n = meta.get("section_count", meta.get("chunk_count", 0))
        summary   = meta.get("video_summary", "")
        desc = f"**{claim_n} claims · {section_n} sections**"
        if summary:
            desc += f"\n\n{summary}"
        desc += f"\n\nTopics: {topic_list or '—'}"
        nodes.append({
            "id":          src,
            "plugin":      None,                     # hub colour
            "version":     "1.0",
            "description": desc,
        })

    # ------------------------------------------------------------------ edges
    links, seen = [], set()

    def add(a, b, w):
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            links.append({"source": a, "target": b, "weight": round(w, 3)})

    # Topic ↔ Source
    for src, slugs in source_topic_slugs.items():
        for s in slugs:
            if s in topic_display:
                add(topic_display[s], src, 0.5)

    # Topic ↔ Topic (co-occurrence, weight = Jaccard similarity of source sets)
    slug_list = [s for s in topic_slugs if topic_display.get(s)]
    for i in range(len(slug_list)):
        for j in range(i + 1, len(slug_list)):
            a, b = slug_list[i], slug_list[j]
            sa = source_topic_slugs and set(
                src for src, slugs in source_topic_slugs.items() if a in slugs)
            sb = set(src for src, slugs in source_topic_slugs.items() if b in slugs)
            union = sa | sb
            if not union:
                continue
            jaccard = len(sa & sb) / len(union)
            if jaccard > 0:
                add(topic_display[a], topic_display[b], max(0.15, jaccard))

    GRAPH_DIR.mkdir(exist_ok=True)
    GRAPH_FILE.write_text(json.dumps({"nodes": nodes, "links": links}, indent=2))
    print(f"✅ graph/graph.json → {len(nodes)} nodes · {len(links)} edges")


if __name__ == "__main__":
    build_graph()
