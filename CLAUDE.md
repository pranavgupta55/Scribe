# Scribe — notes for Claude sessions

**Auto-loaded** by any Claude Code session that runs in this repo. Keep this concise.

## What Scribe is

Local RAG system for YouTube business transcripts.

- `transcripts/` — raw `.txt` + `.meta.json` per video (uploaded by `scribe.sh`)
- `knowledge/` — LLM-extracted topics + facts + connections + embeddings (built by `process.py`)
- `chroma_db/` — ChromaDB vector store
- `server.py` — FastAPI endpoints `/api/rag` and `/api/retrieve` for external callers (Atlas, etc.)

## Post-transcription pipeline (what runs AFTER `scribe.sh`)

1. **`updateDB.sh`** (or `updateDB.sh --rebuild`) — pulls latest transcripts from GitHub, invokes `process.py`.
2. **`process.py`** — 4 passes per transcript:
   - Pass A **SEGMENT** — windowed map-reduce → canonical topic set + ordered sections
   - Pass B **EXTRACT** — 3 chained calls per section (plan → draft → verify)
   - Pass C **STRUCTURE** — 2 calls per topic (plan → fill sections)
   - Pass D **CONNECT** — embedding-clustered topics → cross-source connection sentences
3. **Embeddings** — `nomic-embed-text` via Ollama, into ChromaDB.

Wall-clock: ~5-15 min per transcript (LLM-bound). `updateDB.sh` also does the final `git add knowledge/ && commit && push`.

The 20-30s delay when starting `server.py` is **NOT** the pipeline — it's cold-loading ChromaDB + torch + tokenizer imports. The pipeline runs offline via `updateDB.sh`, not on server startup.

## Alternative pipelines

- **`claudeProcess.py`** at repo root (added 2026-07-04) — Anthropic Claude API port of `process.py` (Opus manager + Haiku worker pool of 8). Same schema, same CLI (`--all`, `--rebuild`, `--connect`, `--rebuild-index`, `<file>`), plus `--knowledge-dir DIR` for dual-run testing. ~$0.20 for a 2k-word transcript, ~$1-2 for a 40k-word one. Requires `ANTHROPIC_API_KEY` env var. Use when Gemini quota is exhausted or when you want fast wall-clock. `process.py` is preserved untouched — same downstream schema — so you can flip between backends.
- Historical note (from an older memory): a prior "haiku pipeline" existed as a laptop-only cheat kept OUT of the repo. That decision is REVERSED — `claudeProcess.py` is now the canonical in-repo Claude path. Ignore any older instruction to keep it local.

## Known bugs

### `process.py` — `NameError: MAX_SECTIONS` at line 1274 (2026-07-04)

```
File "/Users/pranavgupta/VSCode Projects/Scribe/process.py", line 1274, in _equal_slices
    n = max(1, min(len(outline_sections), MAX_SECTIONS) or 1)
NameError: name 'MAX_SECTIONS' is not defined. Did you mean: 'WIN_MAX_SECTIONS'?
```

Only two module-level constants exist: `WIN_MAX_SECTIONS = 6` (line 112) and `MAX_SECTIONS_GLOBAL = 80` (line 119). Line 1274 references bare `MAX_SECTIONS` — a typo / rename artifact.

**Reproduces on**: any transcript that triggers `_equal_slices()` — happened around transcript 54/74 during an `updateDB.sh` run.
**Effect**: process.py crashes mid-batch; already-processed transcripts survive in `knowledge/`, unprocessed ones need to be re-run.
**Fix**: line 1274 — change `MAX_SECTIONS` → `MAX_SECTIONS_GLOBAL` (based on context in surrounding code that uses the global limit for slicing).
**Workaround while unfixed**: run `claudeProcess.py --all` instead — it doesn't share this code path.

## Rules for future Claude sessions

- **Never edit `process.py` in a way that changes the `knowledge/*.json` schema** — downstream (Atlas retrieval) depends on it byte-exactly.
- **`scribe.sh` failures on YouTube's "Sign in to confirm you're not a bot"** = rate-limited. Wait 6-12 h or pass `--cookies-from-browser chrome`. Not a bug.
- **When batch-ingesting**, always use the atomic per-file GH-API upload pattern (see `docs/M4_*.md` for template). Do NOT bulk-commit and push once — that loses resume-safety.
- **Content-category tags** in `.meta.json` distinguish creators:
  - `service_business_lead_gen` — Ericvelch
  - `dtc_brand_building` — Greg LaVecchia
  - `alex_hormozi` — @AlexHormozi main channel
  - `alex_hormozi_clips` — @MoreMozi clip channel

## Useful commands

```bash
# Which transcripts are already in knowledge/?
python3 -c "import json; d = json.load(open('knowledge/sources.json')); print(len(d))"

# Count transcripts on disk
ls transcripts/*.txt | wc -l

# Diff channel vs repo (see docs/M4_*.md for the yt-dlp + sed pattern)
```
