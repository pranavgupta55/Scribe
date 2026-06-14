#!/usr/bin/env python3
"""
Scribe knowledge pipeline.

Chunks transcript .txt files, extracts facts/entities/topics with a local LLM
(Ollama), stores embeddings in ChromaDB, and maintains human-readable Markdown
topic notes. The ChromaDB collections are the primary output for RAG queries.

Collections:
  chunks — full transcript chunks, rich context for retrieval
  facts  — individual extracted claims, precise semantic search

Usage:
    python3 process.py <transcript.txt>    # process a single file
    python3 process.py --all               # process all unprocessed transcripts
    python3 process.py --rebuild           # clear ChromaDB and reprocess everything
    python3 process.py --rebuild-index     # regenerate knowledge/_index.md only
"""

import sys
import json
import re
import time
import shutil
from pathlib import Path
from datetime import datetime

SCRIPT_DIR      = Path(__file__).parent.resolve()
KNOWLEDGE_DIR   = SCRIPT_DIR / "knowledge"
TOPICS_DIR      = KNOWLEDGE_DIR / "topics"
SOURCES_FILE    = KNOWLEDGE_DIR / "sources.json"
CONNECTIONS_FILE= KNOWLEDGE_DIR / "connections.json"
INDEX_FILE      = KNOWLEDGE_DIR / "_index.md"
CHROMA_DIR      = SCRIPT_DIR / ".chroma"
TRANSCRIPTS_DIR = SCRIPT_DIR / "transcripts"

EXTRACT_MODEL = "qwen3:1.7b"
EMBED_MODEL   = "nomic-embed-text"

# Sliding window parameters
CHUNK_WORDS   = 400
OVERLAP_WORDS = 80

# Cosine distance below which two facts are flagged as related/confirming
CONNECTION_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

def check_ollama():
    try:
        import ollama
        result = ollama.list()
        names = [m["model"] for m in result.get("models", [])]
        missing = [m for m in (EXTRACT_MODEL, EMBED_MODEL)
                   if not any(m.split(":")[0] in n for n in names)]
        if missing:
            print(f"❌ Missing Ollama model(s): {', '.join(missing)}")
            print(f"   Run: ollama pull {' && ollama pull '.join(missing)}")
            sys.exit(1)
    except Exception:
        print("❌ Ollama is not running.  Start it with: ollama serve")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Chunking — sliding window, sentence-boundary aware
# ---------------------------------------------------------------------------

def chunk_text(text):
    """
    Split text into overlapping windows of ~CHUNK_WORDS words.
    Boundaries respect sentence endings; each chunk carries OVERLAP_WORDS
    words from the previous chunk so context is not lost at seams.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], []

    for sentence in sentences:
        words = sentence.split()
        if len(current) + len(words) > CHUNK_WORDS and current:
            chunks.append(" ".join(current))
            # carry over tail as overlap
            current = current[-OVERLAP_WORDS:] + words
        else:
            current.extend(words)

    if current:
        chunks.append(" ".join(current))

    return chunks


# ---------------------------------------------------------------------------
# LLM — extraction and synthesis via Ollama
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a precise knowledge extraction assistant. "
    "Respond only with valid JSON. No markdown fences, no explanations."
)

_EXTRACT_TMPL = """Extract structured knowledge from this transcript excerpt.

Text:
{chunk}

Return exactly this JSON (no other text):
{{
  "facts": ["specific verifiable claim", "another claim"],
  "entities": [{{"name": "Name", "type": "person|org|place|product|concept", "description": "one sentence"}}],
  "topics": ["short topic label"]
}}

Constraints:
- facts: atomic, standalone, verifiable claims (max 8)
- entities: named things explicitly mentioned (max 6)
- topics: 1-3 word subject labels covering the main themes (max 4)
"""

_SYNTH_TMPL = """Write a concise knowledge note about the topic "{topic}".

Supporting facts (with source files):
{facts_block}

Write 2-3 paragraphs of Markdown prose that:
1. Summarises what is known about this topic across all sources
2. Flags any contradictions or tensions between sources
3. Cites source filenames inline where it adds clarity

Start directly with the content — no headers, no preamble."""


def llm_extract(chunk):
    """Return {facts, entities, topics} extracted from a text chunk."""
    import ollama
    prompt = _EXTRACT_TMPL.format(chunk=chunk)
    for attempt in range(3):
        try:
            resp = ollama.chat(
                model=EXTRACT_MODEL,
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 600},
            )
            raw = re.sub(r"<think>.*?</think>", "", resp["message"]["content"],
                         flags=re.DOTALL).strip()
            data = json.loads(raw)
            return {
                "facts":    [f for f in data.get("facts",    []) if isinstance(f, str)][:8],
                "entities": [e for e in data.get("entities", []) if isinstance(e, dict)][:6],
                "topics":   [t for t in data.get("topics",   []) if isinstance(t, str)][:4],
            }
        except (json.JSONDecodeError, KeyError):
            if attempt == 2:
                return {"facts": [], "entities": [], "topics": []}
            time.sleep(1)


def llm_synthesize(topic, facts_with_sources):
    """Synthesize a Markdown paragraph from a list of (fact, source) pairs."""
    import ollama
    facts_block = "\n".join(f"- {f}  [{s}]" for f, s in facts_with_sources)
    prompt = _SYNTH_TMPL.format(topic=topic, facts_block=facts_block)
    try:
        resp = ollama.chat(
            model=EXTRACT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 700},
        )
        content = resp["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    except Exception as e:
        return f"_Synthesis failed: {e}_"


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def embed(text):
    import ollama
    return ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def get_collections():
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts  = client.get_or_create_collection("facts",  metadata={"hnsw:space": "cosine"})
    chunks = client.get_or_create_collection("chunks", metadata={"hnsw:space": "cosine"})
    return facts, chunks


def safe_query(col, query_embeddings, n_results, where=None):
    """Query ChromaDB, guarding against empty collections and filter mismatches."""
    count = col.count()
    if count == 0:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    kwargs = {"query_embeddings": query_embeddings, "n_results": min(n_results, count)}
    if where:
        kwargs["where"] = where
    try:
        return col.query(**kwargs)
    except Exception:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


# ---------------------------------------------------------------------------
# Knowledge file helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def topic_slug(topic):
    return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")


def update_topic_file(topic, facts_col):
    """Re-synthesize the Markdown note for a topic from the live ChromaDB."""
    slug = topic_slug(topic)
    topic_file = TOPICS_DIR / f"{slug}.md"

    results = safe_query(facts_col, query_embeddings=[embed(topic)], n_results=25)
    docs, metas = results["documents"][0], results["metadatas"][0]
    if not docs:
        return

    facts_with_sources = [(doc, meta.get("source", "?")) for doc, meta in zip(docs, metas)]
    body = llm_synthesize(topic, facts_with_sources)
    source_list = sorted({m.get("source", "") for m in metas})

    topic_file.write_text(
        f"# {topic.title()}\n\n"
        f"{body}\n\n"
        f"---\n"
        f"_Sources: {', '.join(source_list)}_\n"
    )


def rebuild_index():
    sources = load_json(SOURCES_FILE, {})
    topic_files = sorted(TOPICS_DIR.glob("*.md"))

    lines = [
        "# Scribe Knowledge Index\n\n",
        f"_{len(sources)} source(s) · {len(topic_files)} topic(s)_\n\n",
        "## Topics\n\n",
    ]
    for tf in topic_files:
        name = tf.stem.replace("_", " ").title()
        lines.append(f"- [{name}](topics/{tf.name})\n")

    lines.append("\n## Sources\n\n")
    for fname, meta in sorted(sources.items()):
        topics_str = ", ".join(meta.get("topics", [])) or "—"
        lines.append(
            f"- **{fname}** — "
            f"{meta.get('fact_count','?')} facts · "
            f"{meta.get('chunk_count','?')} chunks · "
            f"topics: {topics_str}\n"
        )

    INDEX_FILE.write_text("".join(lines))


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_transcript(transcript_path, force=False):
    path = Path(transcript_path).resolve()
    name = path.name

    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    TOPICS_DIR.mkdir(exist_ok=True)

    sources = load_json(SOURCES_FILE, {})
    if name in sources and not force:
        print(f"  ℹ️  {name} already processed — skipping.")
        return

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"  ⚠️  {name} is empty — skipping.")
        return

    chunks = chunk_text(text)
    print(f"\n📄 {name} — {len(chunks)} chunks "
          f"({CHUNK_WORDS}-word window, {OVERLAP_WORDS}-word overlap)")

    facts_col, chunks_col = get_collections()
    connections = load_json(CONNECTIONS_FILE, {"connections": []})

    all_topics = set()
    fact_count = 0
    entity_count = 0

    for idx, chunk in enumerate(chunks):
        print(f"  [{idx + 1:>2}/{len(chunks)}] extracting...", end="\r")

        extraction = llm_extract(chunk)
        topics_here = extraction["topics"]
        all_topics.update(topics_here)
        entity_count += len(extraction["entities"])

        # Embed and store the full chunk for RAG context retrieval
        chunks_col.upsert(
            ids=[f"{name}__c{idx}"],
            documents=[chunk],
            embeddings=[embed(chunk)],
            metadatas=[{"source": name, "chunk_idx": idx}],
        )

        # Embed and store each extracted fact
        for f_idx, fact in enumerate(extraction["facts"]):
            if not fact.strip():
                continue

            fact_emb = embed(fact)

            # Cross-source connection detection
            similar = safe_query(
                facts_col,
                query_embeddings=[fact_emb],
                n_results=3,
                where={"source": {"$ne": name}},
            )
            for sim_doc, sim_meta, dist in zip(
                similar["documents"][0],
                similar["metadatas"][0],
                similar["distances"][0],
            ):
                if dist < CONNECTION_THRESHOLD:
                    conn_type = "confirms" if dist < 0.10 else "related"
                    connections["connections"].append({
                        "type":             conn_type,
                        "distance":         round(dist, 4),
                        "fact_new":         fact,
                        "source_new":       name,
                        "fact_existing":    sim_doc,
                        "source_existing":  sim_meta.get("source", "?"),
                    })

            facts_col.upsert(
                ids=[f"{name}__c{idx}__f{f_idx}"],
                documents=[fact],
                embeddings=[fact_emb],
                metadatas=[{
                    "source":    name,
                    "chunk_idx": idx,
                    "topics":    json.dumps(topics_here),
                }],
            )
            fact_count += 1

    print(f"  ✓ {fact_count} facts · {entity_count} entities · "
          f"{len(all_topics)} topics" + " " * 20)

    save_json(CONNECTIONS_FILE, connections)
    new_conns = [c for c in connections["connections"] if c["source_new"] == name]
    if new_conns:
        confirms = sum(1 for c in new_conns if c["type"] == "confirms")
        related  = sum(1 for c in new_conns if c["type"] == "related")
        print(f"  🔗 Cross-source connections: {confirms} confirming, {related} related")

    # Synthesize/update topic Markdown files
    print(f"  → Synthesising {len(all_topics)} topic file(s)...")
    for topic in sorted(all_topics):
        update_topic_file(topic, facts_col)

    # Record in sources registry
    sources[name] = {
        "processed_at": datetime.now().isoformat(),
        "chunk_count":  len(chunks),
        "fact_count":   fact_count,
        "entity_count": entity_count,
        "topics":       sorted(all_topics),
    }
    save_json(SOURCES_FILE, sources)
    rebuild_index()

    print(f"  ✅ {name} complete")


def process_all(force=False):
    if not TRANSCRIPTS_DIR.exists():
        print("❌ transcripts/ directory not found. Run 'git pull' first.")
        sys.exit(1)

    sources   = load_json(SOURCES_FILE, {})
    all_txts  = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    pending   = all_txts if force else [t for t in all_txts if t.name not in sources]

    if not pending:
        print("✅ All transcripts already processed.")
        return

    print(f"Found {len(pending)} unprocessed transcript(s).")
    for t in pending:
        process_transcript(t, force=force)


def do_rebuild():
    """Wipe ChromaDB and sources.json, then reprocess everything from scratch."""
    print("🗑  Clearing ChromaDB index...")
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)

    if SOURCES_FILE.exists():
        bak = SOURCES_FILE.with_suffix(".json.bak")
        SOURCES_FILE.rename(bak)
        print(f"   sources.json backed up to {bak.name}")

    process_all(force=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--rebuild-index":
        rebuild_index()
        print(f"✅ Index rebuilt — {len(load_json(SOURCES_FILE, {}))} sources.")
        return

    check_ollama()

    if arg == "--rebuild":
        do_rebuild()
    elif arg == "--all":
        process_all()
    else:
        p = Path(arg)
        if not p.exists():
            alt = TRANSCRIPTS_DIR / p.name
            if alt.exists():
                p = alt
            else:
                print(f"❌ File not found: {arg}")
                sys.exit(1)
        process_transcript(p)


if __name__ == "__main__":
    main()
