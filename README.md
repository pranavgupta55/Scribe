# Scribe

A personal knowledge pipeline that runs entirely on-device. Transcribe YouTube videos, extract structured knowledge with a local LLM, and store it as vector embeddings for RAG queries — all free, all offline after the first model download.

```
scribe.sh <url>   →   transcript pushed to GitHub
updateDB.sh       →   new facts extracted + embedded, knowledge base updated
```

---

## What it does

### Stage 1 — Transcription (`scribe.sh`)
Downloads the audio from any YouTube URL with `yt-dlp`, transcribes it locally using Qwen3-ASR-1.7B on Apple Silicon (MPS), and pushes the `.txt` file directly to the `transcripts/` folder in this GitHub repo. Nothing is saved locally.

### Stage 2 — Knowledge extraction (`updateDB.sh`)
Pulls the latest transcripts, then for each unprocessed file:

1. **Chunks** the transcript into ~400-word sliding windows with an 80-word overlap so no context is lost at chunk boundaries
2. **Extracts** facts, named entities, and topic labels from each chunk using `qwen3:1.7b` via Ollama, returning structured JSON
3. **Embeds** each extracted fact and each full chunk using `nomic-embed-text` and stores them in ChromaDB (two collections: `facts` for precise queries, `chunks` for full-context RAG)
4. **Detects connections** — for each new fact, queries existing facts from other sources; facts with cosine distance < 0.20 are logged to `knowledge/connections.json` as `confirms` or `related`
5. **Synthesises topic notes** — queries all facts related to each topic and asks the LLM to write a 2-3 paragraph Markdown summary, noting contradictions across sources
6. **Pushes** the updated `knowledge/` directory to GitHub for cross-machine sync

---

## Models

| Model | Purpose | Size | Where |
|---|---|---|---|
| `Qwen3-ASR-1.7B` | Speech-to-text | **3.4 GB** (float16) | HuggingFace, auto-downloads on first `scribe.sh` run |
| `qwen3:1.7b` | Fact extraction + synthesis | **1.1 GB** (Q4_K_M via Ollama) | `ollama pull qwen3:1.7b` |
| `nomic-embed-text` | Semantic embeddings | **274 MB** (via Ollama) | `ollama pull nomic-embed-text` |

**Total first-run download: ~4.8 GB**

Download times (approximate, at 100 Mbps):
- ASR model: ~5 min
- Extraction model: ~1.5 min
- Embedding model: ~25 sec

Subsequent runs load all models from local cache — no re-download.

---

## Speed (Apple M5)

| Step | Speed |
|---|---|
| Audio download | ~5–15 sec (audio only, no video) |
| Transcription | ~12× real-time — **60-min video ≈ 5 min** |
| Extraction per chunk (~400 words) | ~3–5 sec |
| Embedding per text | ~0.3 sec |
| Full `updateDB.sh` on a 1-hr transcript | **~15–20 min** |

The 15-min processing window is one-time per transcript. Once embedded, RAG queries are instant.

---

## Quick start

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login        # one-time GitHub CLI auth
bash setup.sh
source ~/.zshrc
```

See [SETUP.md](SETUP.md) for full details and troubleshooting.

---

## Usage

```bash
# 1. Transcribe a video (uploads to GitHub, nothing saved locally)
scribe.sh https://www.youtube.com/watch?v=...
scribe.sh https://www.youtube.com/watch?v=... my-custom-name   # explicit filename

# 2. Process into knowledge base (run after one or more transcriptions)
updateDB.sh

# 3. Rebuild everything from scratch on a new machine
updateDB.sh --rebuild
```

---

## Output structure

```
transcripts/
  video-title.txt          ← raw transcript (synced via GitHub)

knowledge/
  _index.md                ← auto-generated topic map
  sources.json             ← processing metadata per transcript
  connections.json         ← cross-source fact matches (confirms / related)
  topics/
    machine_learning.md    ← LLM-synthesised note per topic
    ...

.chroma/                   ← local ChromaDB (gitignored, rebuilt with --rebuild)
  chunks/                  ← full transcript chunks for RAG context
  facts/                   ← individual extracted claims
```

`knowledge/` is committed to GitHub and syncs across machines. `.chroma/` is local-only; run `updateDB.sh --rebuild` on a new machine to repopulate it.

---

## Using the knowledge base for RAG

```python
import chromadb

client = chromadb.PersistentClient(path=".chroma")

# Retrieve relevant context for a question
chunks = client.get_collection("chunks")
results = chunks.query(query_texts=["your question here"], n_results=5)
for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
    print(f"[{meta['source']}]\n{doc}\n")

# Or query individual extracted facts directly
facts = client.get_collection("facts")
results = facts.query(query_texts=["your question here"], n_results=10)
```

Pass the retrieved chunks as context to any LLM to answer questions grounded in your transcript archive.
