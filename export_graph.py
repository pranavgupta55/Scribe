#!/usr/bin/env python3
"""
Generate graph/graph.json from the knowledge base for the Scribe graph viewer.

Nodes:
  - Topics  (from knowledge/topics/*.md)  — coloured by dominant source
  - Sources (from knowledge/sources.json) — hub style (no plugin)

Edges:
  - Topic ↔ Source  : topic appears in that source (weight 0.5)
  - Topic ↔ Topic   : ONLY the meaningful relationships found by the LLM connection
                      analysis (Pass D, knowledge/connections.json). The old all-pairs
                      shared-source Jaccard edges are gone — they made the graph complete
                      and added no information (intra-source closeness is already shown by
                      every topic linking to its source node). Cross-source edges are now
                      the interesting part.

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


def fmt_hms(seconds):
    """Format seconds as H:MM:SS (e.g. 13105.9 -> 3:38:25)."""
    if seconds is None:
        return "?"
    s = int(round(float(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def fmt_mins(seconds):
    """Format a processing/transcription duration compactly (e.g. 1098 -> '18 min 18 s')."""
    if seconds is None:
        return "?"
    s = int(round(float(seconds)))
    if s < 60:
        return f"{s} s"
    m, sec = divmod(s, 60)
    return f"{m} min {sec} s" if sec else f"{m} min"


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

        # task 4 — enrich source node: YouTube link, video length, transcription + processing
        # times (read from the .meta.json sidecar at process time), plus the existing counts.
        url   = meta.get("url", "")
        length_str = fmt_hms(meta.get("duration_seconds"))

        desc = f"**{claim_n} claims · {section_n} sections**"
        if summary:
            desc += f"\n\n{summary}"
        if url:
            desc += f"\n\n[Watch on YouTube]({url})"
        desc += (
            f"\n\n- **Video length:** {length_str}"
            f"\n- **Transcription time:** {fmt_mins(meta.get('transcribe_seconds'))}"
            f"\n- **Extraction time:** {fmt_mins(meta.get('process_seconds'))}"
        )
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

    # Topic ↔ Source (every topic links to the source it appears in)
    for src, slugs in source_topic_slugs.items():
        for s in slugs:
            if s in topic_display:
                add(topic_display[s], src, 0.5)

    # Topic ↔ Topic — ONLY the meaningful relationships from the LLM connection analysis
    # (Pass D). The old all-pairs Jaccard edges are gone (they made the graph complete).
    # Edge weight is by relationship strength/type; contradictions and cross-source links
    # are emphasised because they are the most informative.
    KIND_WEIGHT = {"contradiction": 1.0, "builds-on": 0.8, "agreement": 0.7, "related": 0.6}
    conn_edges = []
    if CONNECTIONS_FILE.exists():
        try:
            conn_edges = json.loads(CONNECTIONS_FILE.read_text()).get("edges", [])
        except Exception:
            conn_edges = []

    topic_edge_n = 0
    cross_edge_n = 0
    for e in conn_edges:
        a_slug, b_slug = slug(e.get("a", "")), slug(e.get("b", ""))
        if a_slug not in topic_display or b_slug not in topic_display:
            continue
        w = KIND_WEIGHT.get((e.get("kind") or "related").lower(), 0.6)
        if e.get("cross_source"):
            w = min(1.0, w + 0.15)        # nudge cross-source links up — the interesting ones
            cross_edge_n += 1
        before = len(links)
        add(topic_display[a_slug], topic_display[b_slug], w)
        if len(links) > before:
            topic_edge_n += 1

    GRAPH_DIR.mkdir(exist_ok=True)
    GRAPH_FILE.write_text(json.dumps({"nodes": nodes, "links": links}, indent=2))
    print(f"✅ graph/graph.json → {len(nodes)} nodes · {len(links)} edges "
          f"({topic_edge_n} topic↔topic from Pass D, {cross_edge_n} cross-source)")


if __name__ == "__main__":
    build_graph()
