# Scribe — Pipeline State & Rebuild Runbook

Snapshot: **2026-08-07**. Ground-truth counts from disk/chroma/graph, not from handoff docs.

## Pipeline stages & backends

| Stage | What it does | Backend (actual) | Token cost |
|---|---|---|---|
| 1. Download + transcribe | `scribe.sh` → yt-dlp + ASR → `transcripts/*.txt` | Whisper/local | **Compute** (local) |
| 2. `process.py` extraction | transcript → topics + facts + `knowledge/` + chroma | **Gemini-2.5-flash free tier** → local `qwen3:1.7b` fallback; embeddings `nomic-embed-text` | **Compute / free-LLM** (no Claude tokens) |
| 3. `.scribe-skills/` graph rebuild | claims → concepts → hierarchy → connections → `graph_v2.json` | Claude **Haiku/Sonnet** subagents | **LLM** (Claude subscription) |
| RAG serving | chat + copy view | `server.py` reads `facts_v2`/`chunks_v2` (qwen3-embedding:8b, 4096-d) + `sources.json` | — |

Current sync points: **1997** transcripts on disk · **917** extracted (`sources.json`) · graph_v2 = **917** source nodes · `chunks_v2` = **917/917** sources indexed. Last `process.py` run: **Jul 5**. Newest transcript: **Jul 26**.

## Processing left

| Layer | Work remaining | Backend | Notes |
|---|---|---|---|
| **1. Download + transcribe** | ~**226** AlexHormozi + ~**409** MoreMozi = ~**635** videos in catalogs, not yet on disk | Compute (local) | Only the 2 `.queue/catalog_*.txt` are tracked; Rory/Ericvelch/Greg have no catalog, so their download state is unmeasured. Tasks: `t-36rgya`, `t-3obagr`, `t-wxr7uj`. |
| **2. `process.py` extraction** | **1080** transcripts on disk not in `sources.json` (742 uncategorized · 215 hormozi · 123 moremozi) | Gemini free / local qwen + local embeds | Wall-clock ~5–15 min each. **No Claude spend.** Known bug: `MAX_SECTIONS` NameError mid-batch (see CLAUDE.md). |
| **3a. Graph re-extract (delta)** | Graph is 1080 sources behind disk — Phase 3a extraction on new sources once Stage 2 catches up | Claude Haiku/Sonnet | Heaviest Claude spend (~17M tok/batch historically). Follow `docs/TOKEN_BUDGET.md`. |
| **3b. Graph downstream** | Phase 3b hierarchy → Phase 4 connections → Phase 5 render, for the delta | Claude Haiku (+ mechanical render) | Runs after 3a. |
| **Standalone LLM** | `t-aw2r68` Phase 3a on 20 deferred Rory long-forms; `t-7tjn7y` build `claudeProcess.py` | Claude | Queued tasks. |
| **Mechanical** | Phase 2 re-embed, Phase 5 render, `eval_graph.py` reruns | Local compute | Cheap. |

**Bottom line:** the bulk of remaining work is **compute, not LLM** — ~635 to transcribe + ~1080 to extract, all on Gemini-free/local. Claude-token work is confined to the graph layer (Stage 3), which is in sync at 917 today and only needs re-running *after* the compute backlog is pushed through.

## Chroma / RAG rebuild runbook (new device, or index recovery)

`.chroma/` is **gitignored** (368 MB) — it does **not** travel via git. On a fresh clone the RAG index must be rebuilt from the tracked `transcripts/` + `knowledge/`. Requires **Ollama** running with `qwen3-embedding:8b` (facts/chunks) and `nomic-embed-text` (v1 fallback).

```bash
# 0. prereqs
ollama serve &                      # if not running
ollama pull qwen3-embedding:8b
ollama pull nomic-embed-text

# 1. rebuild v2 collections (facts_v2 from graph claims, chunks_v2 from v1 snapshot)
python3 scripts/rebuild_chroma_v2.py

# 2. index any sources that entered via the graph pipeline and were never chunked
#    (e.g. the Jun-21 Rory batch — chunked from raw transcripts, not the v1 snapshot)
python3 -u scripts/rechunk_missing_into_v2.py            # dry-run: lists gaps
python3 -u scripts/rechunk_missing_into_v2.py --write    # embed + upsert

# 3. verify full coverage (should print "missing: 0")
python3 -c "import chromadb,json; \
ch=chromadb.PersistentClient(path='.chroma').get_collection('chunks_v2'); \
idx={m.get('source') for m in ch.get(include=['metadatas'])['metadatas'] if m and m.get('source')}; \
sj=set(json.load(open('knowledge/sources.json'))); \
print('chunks_v2 sources:',len(idx),'| sources.json:',len(sj),'| missing:',len(sj-idx))"

# 4. serve (regenerates graph_v2.json, opens browser)
./serve.sh                          # http://localhost:8765/graph/index.html
```

### RAG serving notes (post-2026-08-07)
- `server.py` defaults to **v2** retrieval. The old v1 `facts`/`chunks` collections were replaced by `facts_v2`/`chunks_v2` in the Wave 3 rebuild; the v1 code path (`retrieve_structured`, `retrieve`) will crash with `Collection [facts] does not exist` if forced (`use_v2=false`). Leave v2 on.
- Copy view (`/api/rag`, `/api/retrieve`) and chat (`/api/chat`) both use v2. `facts_v2` has no `source` field — source is recovered from `attribution_json`.
