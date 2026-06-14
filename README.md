# Scribe

A personal knowledge pipeline that runs entirely on-device. Transcribe YouTube videos, extract structured knowledge with a local LLM, store it as vector embeddings for RAG, and explore it as an interactive graph.

---

## Commands

| Command | What it does |
|---|---|
| `scribe.sh <url> [name]` | Download + transcribe a YouTube video, push transcript to GitHub |
| `updateDB.sh` | Pull new transcripts, extract knowledge, update GitHub |
| `updateDB.sh --rebuild` | Wipe ChromaDB and reprocess everything from scratch |
| `serve.sh` | Open the knowledge graph **and RAG chat** in your browser |

**Typical workflow:**
```bash
scribe.sh https://www.youtube.com/watch?v=...   # transcribe
updateDB.sh                                      # process into knowledge base
serve.sh                                         # explore the graph
```

---

## Setup

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login        # one-time GitHub CLI auth (browser flow)
bash setup.sh
source ~/.zshrc
```

See [SETUP.md](SETUP.md) for full details and troubleshooting.

---

## What it does

### Stage 1 — Transcription (`scribe.sh`)
Downloads audio from any YouTube URL with `yt-dlp`, transcribes it locally using `whisper-large-v3-turbo` on Apple Silicon (MPS), and pushes the `.txt` to `transcripts/` in this repo via the GitHub API. Nothing saved locally.

### Stage 2 — Knowledge extraction (`updateDB.sh`)
A **two-pass, whole-document** pipeline (`process.py`) — it understands the whole video first, then extracts only specifics that were actually said:

1. **Pass A — Segment.** The entire transcript is read in one `qwen3:1.7b` call (`num_ctx` 16k) to produce a 5–10 section outline (each with premise + conclusion) and a small **canonical topic set (4–8 labels)** for the whole video. Because the model sees everything at once, it merges synonyms instead of exploding into dozens of near-duplicate topics.
2. **Pass B — Extract.** Each section is processed individually for **atomic, grounded claims** (each tagged with one canonical topic), named entities, and subject–predicate–object **relationship triples**. The prompt forbids generic definitions — only what the speaker actually stated, with concrete numbers/names/steps.
3. **Embeds** every section (→ `chunks`) and claim (→ `facts`) with `nomic-embed-text` into ChromaDB.
4. **Detects connections** — claims from different sources within cosine distance 0.20 are logged to `knowledge/connections.json` as `confirms` or `related`.
5. **Assembles topic notes deterministically** — `knowledge/topics/*.md` are built directly from the stored claims (bulleted, section-cited, with a relationships block). **No free-text synthesis call**, so nothing is re-explained or truncated.
6. **Pushes** updated `knowledge/` to GitHub.

### Stage 3 — Graph + RAG chat (`serve.sh`)
Generates `graph/graph.json` and serves an Obsidian-style interactive graph (topics + sources as nodes, PCA-seeded from embeddings). A **Graph | Chat** toggle swaps in a RAG chatbot that answers strictly from your knowledge base — it streams the answer and shows which topic nodes it consulted in real time, each clickable to open in the detail panel.

---

## Models

| Model | Purpose | Size | Storage |
|---|---|---|---|
| `whisper-large-v3-turbo` | Speech-to-text | **~1.6 GB** (float16) | `models/hf/` — auto-downloads on first `scribe.sh` |
| `qwen3:1.7b` | Segment + claim extraction + chat | **1.4 GB** (Q4_K_M) | `models/ollama/` — pulled by `setup.sh` |
| `nomic-embed-text` | Semantic embeddings | **274 MB** | `models/ollama/` — pulled by `setup.sh` |

All models are stored inside the Scribe repo folder under `models/` and are gitignored.

**Total first-run download: ~3.3 GB**

Download times at 100 Mbps:
- ASR model (HuggingFace): ~2 min (on first `scribe.sh` run)
- Ollama models: ~2 min (during `setup.sh`)

> The ASR model is `whisper-large-v3-turbo` as a stand-in for `Qwen/Qwen3-ASR-1.7B`,
> which `transformers` does not yet support natively (`qwen3_asr` architecture). Switch
> `ASR_MODEL` in `qwen_transcribe.py` once native support lands.

---

## Speed (Apple M5)

| Step | Speed |
|---|---|
| Audio download (yt-dlp) | ~5–15 sec |
| Transcription (ASR) | **~10× real-time** — 25-min video ≈ 2.5 min |
| Pass A — segment whole transcript | ~10–20 sec |
| Pass B — extract per section (~5–10 sections) | ~5–10 sec each |
| Embedding per text | ~0.3 sec |
| Full `updateDB.sh` on a 25-min transcript | **~3–5 min** |

---

## Output structure

```
transcripts/
  video-title.txt          ← raw transcripts (synced via GitHub)

knowledge/
  _index.md                ← auto-generated topic map + per-source summaries
  sources.json             ← per-transcript metadata (summary, sections, topics, counts)
  connections.json         ← cross-source claim matches
  topics/
    lead_generation.md     ← deterministic note per topic (claims + relationships)

server.py                  ← static file server + /api/chat RAG endpoint (SSE)

graph/
  graph.json               ← generated by serve.sh / export_graph.py
  index.html               ← interactive graph + chat UI
  graph.js                 ← force-directed graph engine + chat client

models/                    ← all model weights (gitignored)
  hf/                      ← HuggingFace / ASR model cache
  ollama/                  ← Ollama model store

.chroma/                   ← local ChromaDB (gitignored, rebuild with --rebuild)
```

---

## Using the knowledge base for RAG

The built-in chat (`serve.sh` → **Chat** tab) already does grounded RAG over your
archive. To query the collections directly from your own code:

```python
import chromadb, ollama

client = chromadb.PersistentClient(path=".chroma")
q_emb = ollama.embeddings(model="nomic-embed-text", prompt="your question here")["embedding"]

# Full-context retrieval (sections — best for answering questions)
chunks = client.get_collection("chunks")
results = chunks.query(query_embeddings=[q_emb], n_results=5)
for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
    print(f"[{meta['source']} § {meta.get('section_title','')}]\n{doc}\n")

# Precise claim lookup (each claim carries its canonical `topic`)
facts = client.get_collection("facts")
results = facts.query(query_embeddings=[q_emb], n_results=10)
```

> Collections store **precomputed** embeddings, so query with `query_embeddings`
> (embed the query yourself via `nomic-embed-text`), not `query_texts`.

Pass retrieved sections as context to any LLM to answer questions grounded in your archive.

---

## Syncing across machines

Transcripts and the `knowledge/` directory live in GitHub. On any machine:
```bash
git pull                 # get latest transcripts + knowledge
updateDB.sh --rebuild    # repopulate local ChromaDB from existing transcripts
```
