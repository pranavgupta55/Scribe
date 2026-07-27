# RAG v2 — Graph-Augmented Retrieval Design

> Engineering-facing design document for the Scribe chatbot's v2 retrieval
> pipeline. For the architectural decision record see
> [`adrs/decisions/0009-graph-augmented-rag.md`](../ADRs/decisions/0009-graph-augmented-rag.md).

---

## 1. Overview

RAG v2 replaces the flat cosine-over-ChromaDB retrieval used by Scribe's
chatbot with a graph-augmented pipeline that bridges the ChromaDB vector
store to the typed-edge knowledge graph (`graph/graph_v2.json`). The graph
carries 5477 nodes and 19154 typed edges (contradictions, agreements,
builds-on, hosts, illustrates, practices, related) that the v1 chatbot path
could not see. v2 surfaces those relationships — especially contradictions —
in every answer.

Status as of 2026-07-26: `facts_v2` collection is complete; `chunks_v2`
embed is in progress (resume-safe via
`scripts/rebuild_chroma_v2_resume_chunks.py --resume`). The v1 path is
fully intact and continues to serve until `use_v2=true` is passed.

---

## 2. Two Systems

Scribe has two parallel retrieval paths and they must stay separate:

| System | Entry point | ChromaDB collections | Embed model | Status |
|--------|-------------|----------------------|-------------|--------|
| **v1** | `process.py` → `updateDB.sh` | `facts`, `chunks` | `nomic-embed-text` (768-d) | Production |
| **v2** | `scripts/rebuild_chroma_v2.py` | `facts_v2`, `chunks_v2` | `qwen3-embedding:8b` (4096-d) | Chatbot-only |

v1 feeds Atlas's `/api/rag` and `/api/retrieve` endpoints and must not be
disturbed. v2 is gated behind `use_v2: true` on the `/api/chat` endpoint
(see §9).

The `.scribe-skills/` phase pipeline (3a/3b) is a separate offline process
that produces `graph/graph_v2.json` and
`.scribe-skills/phase3b/merged_claims.jsonl`. Those files are inputs to the
v2 rebuild; they are not part of the runtime path.

---

## 3. Data Model

### 3.1 facts_v2 collection

One entry per merged claim (L2/L2a/L2b claim nodes in `graph_v2.json`).

| Field | Type | Notes |
|-------|------|-------|
| `id` | `claim:{claim_id}` | Matches `graph_v2.json` node ID |
| `document` | `str` | Merged claim text (embedded) |
| `topic` | `str` | L0 concept name |
| `level` | `"L2" \| "L2a" \| "L2b"` | Granularity tier |
| `source_count` | `int` | Number of source attributions |
| `attribution_json` | `str` | JSON-serialized list of `{source_file, speaker, speaker_term}` per attribution. ChromaDB metadata values must be scalars; hence the JSON string. |

### 3.2 chunks_v2 collection

Same chunk text and IDs as v1, re-embedded with `qwen3-embedding:8b` so
the vector space matches `facts_v2`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | `{source}__s{i}` | Matches v1 chunk ID |
| `document` | `str` | Section body text (embedded) |
| `source` | `str` | Transcript filename (without `.txt`) |
| `section_idx` | `int` | Zero-based section index |
| `section_title` | `str` | Section heading |
| `premise` | `str` | Section premise sentence |
| `conclusion` | `str` | Section conclusion sentence |

Note: `claim_ids[]` (per-chunk claim membership) was deferred. Chunk
side-effect expansion is resolved at query time via cosine lookup (see §5,
step 6).

### 3.3 graph_v2.json edge kinds

| Kind | Weight | Direction | Usage in retrieval |
|------|--------|-----------|-------------------|
| `contradiction` | 1.0 | undirected | up to 5 neighbors per fact |
| `agreement` | 0.95 | undirected | up to 2 neighbors per fact |
| `builds-on` | 0.8 | undirected | up to 3 neighbors per fact |
| `related` | 0.6 | undirected | **SKIPPED** (too noisy; 9709/19154 total edges) |
| `hosts` | 0.5 | concept → claim | up to 2 parent concepts per fact |
| `illustrates` | 0.5 | concept → example | up to 2 siblings via parent concept |
| `practices` | 0.5 | concept → practice | up to 10 siblings via parent concept |

Edge weights are used by the dedup step (higher weight wins when two
expansion kinds point to the same neighbor node).

---

## 4. Build Phase

### 4.1 Source data

| File | What it is |
|------|-----------|
| `graph/graph_v2.json` | 5477 nodes, 19154 links |
| `.scribe-skills/phase3b/merged_claims.jsonl` | 6055 merged claims with attribution |
| v1 `.chroma/` | Snapshot source for chunk text + metadata |

Only the 917 sources already in the v1 database are embedded in this build.
Processing the remaining ~379 new transcripts is deferred (see §11).

### 4.2 Scripts

| Script | Purpose |
|--------|---------|
| `scripts/rebuild_chroma_v2.py` | Full rebuild: snapshot v1 → wipe → facts_v2 → chunks_v2 |
| `scripts/rebuild_chroma_v2_resume_chunks.py` | Resume-safe chunks-only rebuild with `--resume` flag |

### 4.3 Commands

**Fresh full rebuild:**

```bash
# Prerequisite: ollama must be running with qwen3-embedding:8b pulled.
ollama pull qwen3-embedding:8b

python3 scripts/rebuild_chroma_v2.py
# or skip the confirmation prompt:
python3 scripts/rebuild_chroma_v2.py --force
```

**Resume interrupted chunks_v2 (most common recovery path):**

```bash
python3 -u scripts/rebuild_chroma_v2_resume_chunks.py --resume
```

`--resume` reads existing `chunks_v2` IDs and only embeds missing ones.
Omit `--resume` to drop and rebuild `chunks_v2` from scratch (leaves
`facts_v2` alone).

**Self-test prompt assembly:**

```bash
python3 scripts/prompt_v2.py --test
```

**Verify counts after build:**

```bash
python3 - <<'EOF'
import chromadb
c = chromadb.PersistentClient(".chroma")
print("facts_v2:", c.get_collection("facts_v2").count())
print("chunks_v2:", c.get_collection("chunks_v2").count())
EOF
```

### 4.4 Hardening gotchas (what failed in practice)

These are real failure modes encountered during the initial build run.
Operators need to understand them to diagnose future hangs.

1. **ChromaDB 1.5.9 SharedSystemClient SQLite handle caching.** Even after
   `del client`, ChromaDB's process-level client caches the SQLite handle.
   Opening v2 on the renamed path from the same process caused a read-only
   handle reuse. Fix: `snapshot_v1_chunks()` in `rebuild_chroma_v2.py` runs
   the v1 snapshot in a subprocess so all SQLite state exits before the wipe.

2. **`qwen3-embedding:8b` under `llama-server -c 4096` hangs silently on
   long inputs.** Chunks over ~12k characters hang the embedding server with
   no error. Fix: `MAX_INPUT_CHARS = 7500` in the resume script (roughly
   2000 tokens, well under the 4096-token context limit). The main rebuild
   script uses `MAX_TEXT_CHARS = 30000` (facts are shorter; chunks use the
   resume script).

3. **Timeout-based recovery.** The resume script uses direct HTTP
   (`requests.post(..., timeout=60)`) instead of the `ollama-python` client,
   which can hang indefinitely when `llama-server` dies mid-request. On
   timeout, it retries per-item; items that still fail are skipped and logged
   to `.forge_scratch/scribe_rag_v2/resume_chunks_errors.log`.

4. **Batch size.** The main rebuild uses `BATCH_SIZE = 16`. The resume
   script uses `BATCH_SIZE = 2` to bound damage per hang to at most 2 items.

5. **Actual throughput and timing.** `facts_v2` (6055 entries) finished in
   ~11 minutes. `chunks_v2` runs at ~1.0–1.5 items/sec due to the smaller
   batch size and timeout overhead. Total realistic build time: **45–60
   minutes** (not the 30–45 minutes in the original plan), including recovery
   paths. Plan for this when scheduling a rebuild.

---

## 5. Retrieval Algorithm

Entry point: `scripts/retrieval_v2.py` → `retrieve_v2(query, chroma_dir, graph_path)`

### Step 1 — Query decomposition

Multi-topic queries are split into atomic sub-queries.

- Primary: `qwen3:1.7b` via Ollama, with `think=True` and the same
  `_DECOMPOSE_SYSTEM` prompt as `server.py` so behavior matches v1.
- Fallback 1: Gemini 2.5 Flash (requires `GEMINI_API_KEY` env var).
- Fallback 2: `[query]` — pass through unchanged.
- Time budget: `DECOMPOSE_TIMEOUT_SEC = 2.0` (worker thread with `join(timeout)`).
- Cap: `MAX_SUB_QUERIES = 5`.

### Step 2 — Graph index load (cached)

`graph_v2.json` is loaded once per process and cached in a module-level
dict keyed by absolute path. Subsequent calls within the same server process
pay O(1) dict lookup. The index builds three structures:

- `by_id`: `node_id → node dict`
- `edges_from`: `source_id → [link, ...]`
- `edges_to`: `target_id → [link, ...]`

### Step 3 — Initial retrieval per sub-query

For each sub-query:

1. Embed with `qwen3-embedding:8b` using the build-time prefix:
   `"Instruct: Retrieve semantically similar text.\nQuery: "`.
2. Query `facts_v2`: top `N_FACTS_PER_SQ = 3` results.
3. Query `chunks_v2`: top `N_CHUNKS_PER_SQ = 3` results.
4. ChromaDB returns cosine distance; convert to similarity: `sim = 1.0 - dist`.

### Step 4 — Merge across sub-queries

- Union by ID. Facts merged by `node_id`; chunks by `chunk_id`.
- Similarity = max across sub-queries.
- `sub_query_indices` = union of sub-query indices that returned each item.

### Step 5 — Graph expansion per retrieved fact

For each retrieved claim node, expand via graph edges with kind-specific caps:

| Kind | Cap | How |
|------|-----|-----|
| `contradicts` | 5 | `contradiction` edges, both directions (undirected) |
| `agrees` | 2 | `agreement` edges, both directions (undirected) |
| `builds_on` | 3 | `builds-on` edges, both directions (undirected) |
| `hosts` | 2 | `hosts` edges where the OTHER endpoint is the parent concept (`edges_to[claim]`) |
| `illustrates` | 2 | walk each parent concept → follow `illustrates` edges from concept |
| `practices` | 10 | walk each parent concept → follow `practices` edges from concept |
| `related` | 0 | **SKIPPED** — 9709/19154 total edges, too noisy |

**No forced multi-hop.** If a fact has zero neighbors of a given kind, that
kind is simply empty. The system does not fall back to a different kind or
follow secondary hops.

### Step 6 — Chunk side-effect expansion

After dedup (step 7), each expanded neighbor's text is embedded and used to
query `chunks_v2` with `n_results=1`. If `similarity >= SIDE_EFFECT_SIM_THRESHOLD
(0.5)` and the chunk is not already retrieved, it is added as a side-effect
chunk. Capped at `MAX_SIDE_EFFECT_CHUNKS = 8` (keep highest-similarity).

Cost: approximately 10–20ms per neighbor at query time (one embed call +
one ChromaDB query per neighbor). This is the trade-off for deferring
`claim_ids[]` precomputation from build time to query time.

### Step 7 — Dedup

**Step 7a — Union by node ID.** If the same neighbor `node_id` appears
under multiple kinds for a single fact, keep the occurrence with the highest
`_KIND_WEIGHT` and drop the others.

**Step 7b — Text cosine similarity within kind.** For each kind list, embed
all neighbor texts (batched) and drop the lower-weight member of any pair
with cosine similarity `>= SIM_DEDUP_THRESHOLD (0.95)`.

### Step 8 — Sort and return

- Facts sorted by `similarity` descending.
- Chunks: non-side-effect first (similarity desc), then side-effect (similarity desc).

**Return schema:**

```python
{
    "sub_queries": [str, ...],
    "facts": [
        {
            "node_id": str,          # e.g. "claim:1042"
            "text": str,
            "topic": str,
            "level": "L2" | "L2a" | "L2b",
            "source_count": int,
            "similarity": float,
            "sub_query_indices": [int, ...],
            "expansions": {
                "contradicts":  [neighbor, ...],
                "agrees":       [neighbor, ...],
                "builds_on":    [neighbor, ...],
                "hosts":        [neighbor, ...],
                "illustrates":  [neighbor, ...],
                "practices":    [neighbor, ...],
            },
        },
        ...
    ],
    "chunks": [
        {
            "chunk_id": str, "source": str, "section_idx": int,
            "section_title": str, "premise": str, "conclusion": str,
            "text": str, "similarity": float,
            "sub_query_indices": [int, ...], "is_side_effect": bool,
        },
        ...
    ],
    "meta": {
        "decompose_ms": int, "retrieve_ms": int, "expand_ms": int,
        "dedup_ms": int, "n_dedup_dropped_by_id": int,
        "n_dedup_dropped_by_sim": int, "n_side_effect_chunks": int,
    },
}
```

Each `neighbor` record:

```python
{
    "node_id": str,
    "text": str,
    "kind": str,        # e.g. "contradicts"
    "weight": float,
    "confidence": float,
    "source": str | None,   # best-effort source file attribution
}
```

---

## 6. Prompt Assembly

Entry point: `scripts/prompt_v2.py` → `assemble_prompt(query, retrieval) -> (system, user_message)`

The system prompt is `SYSTEM_PROMPT_V2` (a constant in `prompt_v2.py`).
The user message is an XML block formatted per Anthropic's canonical
long-context pattern: retrieved context first, then the user query at the
bottom.

### 6.1 XML structure

```xml
<retrieved_context>

  <fact index="1" node_id="claim:1042" topic="Founder Focus"
        level="L2" source_count="4" similarity="0.87">
    <claim>A founder who tries to run two businesses simultaneously
    almost always underperforms in both.</claim>

    <contradicts weight="1.00" confidence="1.00" node_id="claim:88">
      <claim>Portfolio operators who split attention across three
      portfolio companies consistently outperform single-focus
      operators in the same sector.</claim>
    </contradicts>

    <agrees weight="0.95" confidence="0.90" node_id="claim:201">
      <claim>Single-threaded ownership is the most reliable predictor
      of startup velocity in the pre-PMF stage.</claim>
    </agrees>

    <builds_on weight="0.80" confidence="0.85" node_id="claim:55">
      <claim>Attention is a finite resource; every decision to pursue
      an opportunity costs a decision not to pursue another.</claim>
    </builds_on>

    <hosts weight="0.50" node_id="concept:focus">
      <concept>Founder Focus</concept>
    </hosts>

    <illustrates weight="0.50" node_id="example:gym_chain">
      <example>Alex sold two of his gym locations to regain full
      attention on scaling the third. _from hormozi_gym_talk.txt_</example>
    </illustrates>

    <practices weight="0.50" node_id="practice:weekly_review">
      <practice>Weekly single-priority review: identify the one lever
      that, if pulled, unlocks the rest this week.</practice>
    </practices>

  </fact>

  <chunk index="1" chunk_id="hormozi_gym_talk__s3" source="hormozi_gym_talk"
         section_idx="3" similarity="0.81">
    <title>Consolidating to a single location</title>
    <premise>Alex describes the moment he decided to sell two gyms.</premise>
    <body>So I had three gyms and I thought I was killing it. I had
    revenue coming from all three. And then I looked at my calendar
    and realized I hadn't been to any of them in three weeks...</body>
    <conclusion>Selling the two underperforming locations freed six
    hours per day and doubled the remaining gym's revenue in 90 days.</conclusion>
  </chunk>

</retrieved_context>

<user_query>
How does Hormozi think about founder focus and attention?
</user_query>
```

Key structural rules:
- Expansion kinds emitted in fixed order: `contradicts → agrees → builds_on → hosts → illustrates → practices`.
- Empty kinds are omitted entirely (no empty tags).
- Chunk body truncated at `_LIMIT_CHUNK_BODY = 2500` chars, with sentence-boundary awareness.
- Fact/neighbor claim text truncated at `_LIMIT_REGULAR = 1200` chars, contradicts at `_LIMIT_CONTRADICTS = 2000`.
- All text XML-escaped (`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`).
- Non-side-effect chunks listed first (similarity desc), then side-effect chunks.
- `<user_query>` appears LAST per Anthropic long-context guidance.

### 6.2 System prompt rules (abbreviated)

The full `SYSTEM_PROMPT_V2` constant is in `scripts/prompt_v2.py`. Key rules:

1. **Contradictions first.** If any `<contradicts>` tag appears in context,
   output a `<conflicts>` block first — one line per pair — before the answer.
   Never drop or reconcile contradictions.
2. **Inline citations.** Every factual claim cited with `[F<n>]` (facts) or
   `[C<n>]` (chunks).
3. **Edge weight hierarchy.** Contradiction (1.0) and agreement (0.95) carry
   most weight. Builds-on (0.8), hosts/illustrates/practices (0.5) are
   scaffolding.
4. **Greetings escape hatch.** Short social messages bypass all retrieval
   context — respond conversationally in 1–2 sentences.

---

## 7. Generation and Streaming

### 7.1 LLM chain

| Priority | Model | Condition |
|----------|-------|-----------|
| 1 | Gemini 2.5 Flash (`gemini-2.5-flash`) | Default; streams via `generate_stream()` |
| 2 | Gemini 2.0 Flash (`gemini-2.0-flash`) | Fallback if 2.5 Flash quota exceeded |
| 3 | Local `qwen3:1.7b` (Ollama) | Fallback if both Gemini models fail; streams via `qwen_stream()` |

### 7.2 System prompt injection

`generate_stream(client, entries, system=None)` and
`qwen_stream(entries, system=None)` both accept an optional `system=` kwarg.
When `use_v2=True`, `_v2_chat_flow()` returns a `"system"` key containing
`SYSTEM_PROMPT_V2`, which is passed via this kwarg. The v1 constant
`_SYSTEM` is untouched.

### 7.3 SSE event types

The `/api/chat` endpoint streams Server-Sent Events. All event types are
JSON-encoded in `data:` fields:

| Event type | Payload |
|------------|---------|
| `nodes` | `{type: "nodes", nodes: [{name, slug, source, count}, ...]}` |
| `sources` | `{type: "sources", sources: [filename, ...]}` |
| `debug` | `{type: "debug", sub_queries: [...], system_tokens: int, history_tokens: int, v2: {...}}` |
| `backend` | `{type: "backend", backend: "gemini-2.5-flash" \| ...}` |
| `token` | `{type: "token", text: str}` |
| `notice` | `{type: "notice", text: str}` |
| `error` | `{type: "error", message: str}` |
| `done` | `{type: "done"}` |

---

## 8. Observability

When `use_v2=True`, the `debug` SSE event carries an additional `v2`
sub-object:

```json
{
  "type": "debug",
  "sub_queries": ["What is founder focus?"],
  "system_tokens": 412,
  "history_tokens": 0,
  "v2": {
    "retrieval_meta": {
      "decompose_ms": 340,
      "retrieve_ms": 210,
      "expand_ms": 45,
      "dedup_ms": 120,
      "n_dedup_dropped_by_id": 2,
      "n_dedup_dropped_by_sim": 1,
      "n_side_effect_chunks": 3
    },
    "prompt_stats": {
      "total_chars": 6840,
      "n_facts": 3,
      "n_chunks": 9,
      "n_expansions_by_kind": {
        "contradicts": 4,
        "agrees": 2,
        "builds_on": 3,
        "hosts": 2,
        "illustrates": 2,
        "practices": 8
      },
      "n_contradictions": 4
    }
  }
}
```

The dev panel (frontend) can surface these stats. `eval_v1_vs_v2.py`
captures them for offline comparison.

---

## 9. Client Integration

**Single endpoint, body flag — no new endpoint.**

```http
POST /api/chat
Content-Type: application/json

{
  "query": "How does Hormozi think about founder focus?",
  "use_v2": true
}
```

When `use_v2` is `false` or omitted, the v1 flow is unchanged. When
`use_v2=true` and the v2 modules are unavailable (import error), the server
emits a `notice` SSE event explaining the fallback and continues with v1.

The v2 modules are imported lazily at server startup:

```python
# server.py (top of file)
try:
    import retrieval_v2 as _rv2
    import prompt_v2 as _pv2
    _V2_AVAILABLE = True
except ImportError as e:
    _rv2 = None
    _pv2 = None
    _V2_AVAILABLE = False
```

This means `server.py` starts successfully even if the v2 scripts are
missing; it simply cannot serve `use_v2=true` requests.

---

## 10. Eval Harness

`scripts/eval_v1_vs_v2.py` runs a side-by-side evaluation of v1 vs v2
against a running `server.py` instance.

**Usage:**

```bash
# Against the default local server:
python3 scripts/eval_v1_vs_v2.py

# Against a different host:
python3 scripts/eval_v1_vs_v2.py --server http://localhost:8765

# Custom query set (one query per line):
python3 scripts/eval_v1_vs_v2.py --queries path/to/queries.txt
```

**Built-in queries** (6 queries covering single-topic, multi-topic, and
opinion/contradiction prompts):

```
How does Hormozi think about founder focus and attention?
What's the difference between building a business and building an audience?
When should I raise prices vs add a guarantee?
What do successful entrepreneurs say about mentorship?
Compare hiring virtual assistants to hiring local employees.
Is drop-servicing a real business or just arbitrage?
```

**Output:** `.forge_scratch/scribe_rag_v2/eval_v1_v2.json`

Per-query capture:

- `v1`: consulted topics, source count, first 400 chars of answer, wall ms.
- `v2`: retrieval meta, prompt stats (n_facts, n_chunks, n_contradictions,
  total_chars), first 400 chars of answer, wall ms.

This file is the input for Phase 6 quality eval (deferred; see §11).

---

## 11. Deferred / Open Items

| Item | Notes |
|------|-------|
| **Phase 6 quality eval** | Run `eval_v1_vs_v2.py` once both collections are complete; score answer quality. Tracked in task `t-fokp1z`. Was deferred while 7d rate limit was near cap. |
| **Process 379 new transcripts** | The v2 build covers the 917 sources already in v1. New transcripts need `process.py` / `claudeProcess.py` first, then a `chunks_v2` incremental add. |
| **Reranker** | No reranker in v2. Candidates: BGE-reranker-v2-m3 (local), or a Gemini/Claude LLM reranker. Deferred until baseline quality is measured. |
| **Config knob sweeps** | `MAX_CONTRADICTS`, `MAX_AGREES`, `SIM_DEDUP_THRESHOLD`, `SIDE_EFFECT_SIM_THRESHOLD`, `N_FACTS_PER_SQ`, `N_CHUNKS_PER_SQ` are all tunable. Eval harness supports this; sweeps deferred. |
| **`claim_ids[]` in chunks_v2 metadata** | Deferred at build time; chunk side-effect works via runtime cosine. Precomputing at build time would speed up query-time expansion but requires a separate build pass. |
| **chunks_v2 rebuild completion** | Task `t-pprrm1` is still active pending full `chunks_v2` completion. |

---

## 12. Related

- **ADR:** [`adrs/decisions/0009-graph-augmented-rag.md`](../ADRs/decisions/0009-graph-augmented-rag.md) — decision + alternatives + rationale
- **Tasks:** `t-pprrm1` (this rebuild), `t-fokp1z` (Phase 6 quality eval)
- **Source scripts:**
  - `scripts/rebuild_chroma_v2.py` — main rebuild
  - `scripts/rebuild_chroma_v2_resume_chunks.py` — resume-friendly chunks-only rebuild
  - `scripts/retrieval_v2.py` — `retrieve_v2()` function
  - `scripts/prompt_v2.py` — `assemble_prompt()` + `SYSTEM_PROMPT_V2`
  - `scripts/eval_v1_vs_v2.py` — v1-vs-v2 eval harness
  - `server.py` — `_v2_chat_flow()`, `use_v2` body flag, `generate_stream(system=)`, `qwen_stream(system=)`
- **Data:**
  - `graph/graph_v2.json` — 5477 nodes, 19154 links
  - `.scribe-skills/phase3b/merged_claims.jsonl` — 6055 merged claims
  - `.chroma/` — v2 ChromaDB (facts_v2, chunks_v2)
  - `.chroma_v1_backup_*/` — timestamped v1 backup (never deleted)
  - `.forge_scratch/scribe_rag_v2/rebuild_errors.log` — build error log
  - `.forge_scratch/scribe_rag_v2/eval_v1_v2.json` — eval output
