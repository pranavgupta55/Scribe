# Scribe

A personal knowledge pipeline that runs entirely on-device. Transcribe YouTube videos, extract structured knowledge with a local LLM, store it as vector embeddings for RAG, and explore it as an interactive graph.

> 📐 **See [ARCHITECTURE.md](ARCHITECTURE.md)** for the v2 knowledge-graph design — node layers (L0 Concepts / L1 Frameworks / L2 Claims / L3 Examples / L4 Practices), the transcript → graph pipeline, and how cross-source claim agreement is tracked.

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

# For the RAG chat: a free Google Gemini key (https://aistudio.google.com/apikey)
echo 'export GEMINI_API_KEY="AIza..."' >> ~/.zshrc && source ~/.zshrc
```

See [SETUP.md](SETUP.md) for full details and troubleshooting.

---

## What it does

### Stage 1 — Transcription (`scribe.sh`)
Downloads audio from any YouTube URL with `yt-dlp`, transcribes it locally using `whisper-large-v3-turbo` on Apple Silicon (MPS), and pushes the `.txt` to `transcripts/` in this repo via the GitHub API. Nothing saved locally.

### Stage 2 — Knowledge extraction (`updateDB.sh`)
A **chained, multi-step** pipeline (`process.py`) running entirely on local `qwen3:1.7b`. Rather than one big call per stage, it decomposes each pass into short, single-objective calls — a "thinking" scaffold that lifts a 1.7B model well above its single-shot quality:

1. **Pass A — Segment.** `A1` reads the whole transcript (`num_ctx` 16k) and names a **canonical topic set (10–16 labels)**, merging synonyms instead of exploding into duplicates; `A2` splits the video into **8–14 sections** conditioned on those topics (retries if it under-segments).
2. **Pass B — Extract + verify.** Per section, `B1` over-drafts candidate claims, then `B2` **verifies/refines each against the transcript** with a rubric — dropping vague or model-invented statements, never inverting the source. Each surviving claim is grounded, specific, and tagged with one canonical topic.
3. **Pass C — Structure.** Per topic, the model selects **3–5 of an 11-section catalog** (Key Takeaway, Key Numbers, How-To, Why It Works, Implications, Contradictions, Caveats, …) by explicit criteria and fills them **only** from that topic's claims. A guard drops any bullet containing a number absent from the claims, blocking fabrication.
4. **Embeds** every section (→ `chunks`) and claim (→ `facts`) with `nomic-embed-text` into ChromaDB.
5. **Detects connections** — claims from different sources within cosine distance 0.20 are logged to `knowledge/connections.json` as `confirms` or `related`.
6. **Assembles topic notes deterministically** from the structured output (section-cited, no free-text re-explanation) and **pushes** `knowledge/` to GitHub.

The grading rubric and few-shot bank live in `evals/claim_evals.md`.

### Stage 3 — Graph + RAG chat (`serve.sh`)
Generates `graph/graph.json` and serves an Obsidian-style interactive graph (topics + sources as nodes, PCA-seeded from embeddings). A **Graph | Chat** toggle swaps in a RAG chatbot: retrieval stays **local** (nomic embeddings + ChromaDB) and surfaces which topic nodes were consulted in real time (clickable), while generation streams from **Google Gemini Flash** (free tier) for a sharp, grounded answer. Requires `GEMINI_API_KEY` (see Setup).

---

## Models

| Model | Purpose | Size | Storage |
|---|---|---|---|
| `whisper-large-v3-turbo` | Speech-to-text | **~1.6 GB** (float16) | `models/hf/` — auto-downloads on first `scribe.sh` |
| `qwen3:1.7b` | Segment + claim extraction (local) | **1.4 GB** (Q4_K_M) | `models/ollama/` — pulled by `setup.sh` |
| `nomic-embed-text` | Semantic embeddings (local) | **274 MB** | `models/ollama/` — pulled by `setup.sh` |
| `gemini-2.5-flash` | RAG chat generation | — (cloud, free tier) | Google AI Studio — needs `GEMINI_API_KEY` |

The three local models are stored inside the Scribe repo folder under `models/` and are gitignored. Only the interactive chat calls the cloud; transcription, extraction, and retrieval all run on-device.

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
