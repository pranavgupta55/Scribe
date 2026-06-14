# Scribe — Knowledge Extraction Redesign Plan

**Scope:** Fix how knowledge is collected/processed/stored in `process.py` (and the
downstream `export_graph.py` reader). This is a research + design document. The main
agent implements from it. No implementation here.

**Target file:** `/Users/pranavgupta/VSCode Projects/Scribe/process.py`

---

## 1. Diagnosis — exactly what is wrong (with file/line evidence)

### 1a. Descriptions truncate mid-sentence ("...is critical for")

**Cause: `num_predict` output cap is too small for the verbose prose it is being
asked to write.**

- `process.py:174` — `llm_synthesize()` calls Ollama with
  `options={"temperature": 0.3, "num_predict": 700}`.
- `num_predict` is a hard cap on output tokens. When the model's verbose 3-paragraph
  answer exceeds ~700 tokens, Ollama stops generation **mid-token/mid-sentence** with
  no error.
- Evidence in the output files:
  - `knowledge/topics/lead_management_efficiency.md:3` ends `"...This efficiency is critical for"` (hard stop).
  - `knowledge/topics/financial_claims.md:5` ends `"Contradictions arise between the speaker's claims of $1"` (hard stop).
- Secondary factor: **qwen3:1.7b is a thinking model** (confirmed via `ollama show`:
  `Capabilities: thinking`, default `temperature 0.6`). When thinking is active, the
  model spends output tokens inside `<think>...</think>` before the visible answer.
  The code strips think blocks post-hoc (`process.py:177`) but the think tokens still
  **count against `num_predict`**, so the real answer gets even less budget. With
  thinking on, a 700-token cap can be almost entirely consumed by reasoning.

**Confirmed Ollama behavior (web research):** `num_predict` sets the max output; if the
answer would be longer it is simply cut off mid-sentence. `num_predict = -1` means no
limit. (Sources: Ollama issue #7691; technovangelist num_predict notes.)

### 1b. Output is verbose textbook prose, not extracted specifics

**Cause: the synthesis prompt explicitly asks for explanatory prose.**

- `process.py:123-133` — `_SYNTH_TMPL` says: *"Write 2-3 paragraphs of Markdown prose
  that: 1. Summarises what is known about this topic... 2. Flags any contradictions...
  3. Cites source filenames."*
- "Write 2-3 paragraphs of prose" + "summarise what is known" is a recipe for
  **abstractive summarization** — the model re-explains the concept in general terms.
- Evidence: `knowledge/topics/business_efficiency.md` reads like an essay
  ("The topic of business efficiency is closely tied to the integration of AI and
  data-driven strategies to optimize operations...") — generic re-teaching, not
  video-specific extraction.
- Research framing: using an LLM to *summarize concepts* is abstractive and tends to
  reproduce the model's prior knowledge. What the user wants is **extractive / atomic
  claim extraction** — minimal, decontextualized units of information lifted directly
  from the transcript, plus **subject-predicate-object triples** for graph structure.
  (Sources: arxiv 2604.02866 atomic propositions for triplet extraction; Medium KG
  triple extraction.)

### 1c. Topic explosion — 42 near-duplicate topics from a 25-min video

**Cause: per-chunk extraction with no global view of the video.**

- `process.py:41-42` — chunking is `CHUNK_WORDS=400`, `OVERLAP_WORDS=80` →
  `sources.json` shows **21 chunks** for this transcript.
- `process.py:314-319` — the pipeline loops over each of the 21 chunks and calls
  `llm_extract()`, which (`process.py:120`) is told to return up to **4 topic labels per
  chunk**. 21 chunks × up to 4 labels = up to 84 candidate labels.
- `process.py:319` — `all_topics.update(topics_here)` unions them with **no
  deduplication, merging, or canonicalization**. A 1.7B model with only a 400-word
  window invents a fresh label each time ("Business Efficiency", "Business Growth",
  "Business Improvement", "Revenue Growth", "Income Generation" — all the same idea).
- Result (`sources.json`): **42 topics / 89 facts / 68 entities** for one video.
- The model **never sees the whole video**, so it cannot recognize that chunk 3 and
  chunk 17 are about the same theme. Topic identity is impossible to maintain locally.

### 1d. Input is needlessly fragmented — the whole video fits in context

- **Model:** `qwen3:1.7b`, **context length = 40960 tokens** (confirmed via
  `ollama show qwen3:1.7b`).
- **Transcript size:** `the_lazy_way_i_make_money_with_ai_2026.txt` = **6,064 words /
  32,242 chars** ≈ **8,000–9,000 tokens**.
- The entire 25-min transcript fits in a single pass with ~30K tokens to spare. There
  is **no technical reason to fragment it into 21 chunks** for understanding.
- **BUT** `process.py` never sets `num_ctx`. Ollama's default context is **2048–4096
  tokens** and it **silently truncates input from the front** when exceeded. So even if
  we passed the whole transcript today, Ollama would silently drop most of it. Any new
  design **must set `num_ctx` explicitly** (Source: Ollama FAQ / serverman num_ctx guide).

### 1e. Downstream effects (no action needed beyond the reader)

- `connections.json` is empty (`{"connections": []}`) — single source so far; logic in
  `process.py:337-358` is fine and stays.
- `export_graph.py` reads `topics/*.md` first paragraph as node description
  (`export_graph.py:146`) and `sources.json["topics"]` for edges
  (`export_graph.py:121`). The new format keeps these fields, so the graph keeps
  working with only minor reader tolerance (see §7).

---

## 2. New architecture (one coherent design)

**Principle:** Understand the whole video first, then extract specific, grounded
knowledge tied to what the video *actually said* — never re-teach generic concepts.

Replace the "21 chunks × per-chunk extract → union topics → abstractive synth" loop
with a **two-pass, whole-document pipeline**:

```
transcript.txt
   │
   ├─ Pass A: SEGMENT  (1 LLM call, whole transcript in context)
   │     → outline: 5–10 sections, each with title + premise + conclusion
   │     → 4–8 canonical topic labels for the WHOLE video (model picks, deduped)
   │
   ├─ Pass B: EXTRACT  (1 LLM call per section, section text in context)
   │     → atomic claims (decontextualized, grounded in transcript)
   │     → entities (named things actually mentioned)
   │     → triples (subject | predicate | object) for graph edges
   │     → each claim tagged with one of the canonical topics from Pass A
   │
   ├─ EMBED + STORE  (ChromaDB: chunks=sections, facts=claims, + triples metadata)
   │
   └─ WRITE topic .md  (deterministic assembly from stored claims — NO synthesis LLM call)
```

### Why this fixes each problem

- **Truncation (1a):** the topic .md files are now **assembled from stored data
  deterministically** (bullet lists of already-extracted claims). No long free-text
  generation → nothing to truncate. Every LLM call that *does* run gets a generous,
  explicit `num_predict` (see §5).
- **Verbosity (1b):** we stop asking the model to "write paragraphs." Pass B extracts
  short atomic claims; the .md file lists them verbatim. No abstractive prose.
- **Topic explosion (1c):** Pass A produces **one** canonical topic list for the entire
  video (target 4–8, hard cap 10), because the model sees the whole thing at once and
  is explicitly told to merge synonyms. Pass B may only tag claims with topics **from
  that fixed list** — it cannot invent new ones.
- **Fragmentation / input truncation (1d):** Pass A loads the full transcript with
  `num_ctx=16384` (well above the ~9K-token transcript, well under the 40960 max).
  Sections are the natural unit thereafter.

### Sectioning detail

- Pass A returns a JSON outline. Each section has `{title, premise, conclusion,
  start_marker}` where `start_marker` is the first ~8 words of the section so we can
  split the raw transcript on it (robust, no timestamps available).
- If `start_marker` matching fails for a section, fall back to splitting the transcript
  into N roughly-equal slices by the returned section count. (Graceful degradation.)
- Section count target: **5–10**. A 25-min video → ~6–8 sections is ideal.

---

## 3. Concrete system prompts and templates

> All prompts prepend `/no_think` is **not** used; instead we disable thinking via the
> Ollama option `think=False` (cleaner, see §5). Prompts assume thinking is OFF.

### 3a. Pass A — SEGMENT (whole transcript → outline + canonical topics)

**System:**
```
You are a precise transcript analyst. You read an entire video transcript and break it
into its natural sections. You output ONLY valid JSON — no markdown, no commentary, no
code fences. Every field must be grounded in what the transcript actually says. Do not
add outside knowledge. Do not explain general concepts.
```

**User template:**
```
Here is the full transcript of a video.

TRANSCRIPT:
{full_transcript}

Break this video into its 5 to 10 natural sections in the order they occur. For the
whole video, also choose a SHORT list of 4 to 8 canonical topic labels. Merge synonyms
into ONE label (e.g. do not list both "Lead Generation" and "Getting Leads"). Topic
labels are 1-3 words, Title Case.

Return EXACTLY this JSON and nothing else:
{
  "video_summary": "one sentence: what specific thing this video teaches or argues",
  "topics": ["Topic A", "Topic B"],
  "sections": [
    {
      "title": "short section title",
      "start_marker": "first 8 words of this section, copied verbatim from the transcript",
      "premise": "the specific setup/claim the speaker makes to OPEN this section (one sentence, from the transcript)",
      "conclusion": "the specific takeaway the speaker lands on to CLOSE this section (one sentence, from the transcript)"
    }
  ]
}

Rules:
- premise and conclusion must be SPECIFIC to this video, quoting concrete numbers,
  names, and steps the speaker gives. Never write generic definitions.
- topics: max 8, deduplicated, no near-synonyms.
- sections: between 5 and 10.
```

### 3b. Pass B — EXTRACT (one section → claims, entities, triples)

**System:**
```
You extract specific, factual knowledge from a transcript section. You output ONLY
valid JSON — no markdown, no commentary, no code fences. Extract ONLY what is stated in
the text. Never add general background, definitions, or explanations the speaker did
not give. If the section contains no concrete claim, return empty arrays.
```

**User template:**
```
This is one section of a video titled "{section_title}".
The video's canonical topics are: {topic_list}.

SECTION TEXT:
{section_text}

Extract the SPECIFIC knowledge stated in this section.

Return EXACTLY this JSON and nothing else:
{
  "claims": [
    {
      "claim": "one specific, standalone, verifiable statement made in the text (include concrete numbers, names, steps)",
      "topic": "the single best-fitting label from the canonical topics above"
    }
  ],
  "entities": [
    {"name": "Name", "type": "person|org|product|tool|method|metric", "mention": "the exact phrase the transcript uses it in"}
  ],
  "triples": [
    {"subject": "X", "predicate": "verb phrase", "object": "Y"}
  ]
}

Rules:
- claims: atomic (one fact each), decontextualized (understandable alone), max 6.
  Each claim MUST be something THIS speaker actually said — quote the specifics.
- Do NOT write definitions of common terms. Do NOT explain what AI/marketing/etc. are.
- topic for each claim MUST be exactly one of: {topic_list}.
- entities: only things explicitly named in this section, max 6.
- triples: subject-predicate-object derived from the claims, max 6. Keep subject/object
  short (the actual named thing), predicate a short verb phrase.
```

### 3c. No synthesis LLM call

`llm_synthesize()` is **deleted**. Topic .md files are assembled deterministically from
stored claims (§6). This is the single biggest win against both verbosity and truncation.

---

## 4. Why thinking mode is disabled

- `qwen3:1.7b` ships with thinking ON (`ollama show` → `thinking` capability,
  modelfile `temperature 0.6`).
- Research finding: **Qwen3 thinking mode does not support reliable structured output**,
  and thinking tokens consume the output budget (Hugging Face Qwen3-1.7B card; Medium
  "Ollama + Qwen3 structured output").
- We disable thinking for **all** calls in this pipeline via the Ollama `think=False`
  argument (Ollama Python ≥0.4 supports `think=` on `chat`). If the installed `ollama`
  Python package version does not accept `think=`, fall back to prepending `/no_think`
  to the user message (soft switch) AND keep the existing `<think>` regex strip as a
  belt-and-suspenders guard.

---

## 5. Exact Ollama option params

### Pass A — SEGMENT (`llm_segment`)
```python
ollama.chat(
    model="qwen3:1.7b",
    messages=[{"role": "system", "content": SEGMENT_SYSTEM},
              {"role": "user",   "content": segment_user}],
    format="json",
    think=False,                       # disable thinking (see §4)
    options={
        "num_ctx":     16384,          # hold whole ~9K-token transcript + headroom
        "num_predict": 2048,           # outline JSON is small; generous so it never truncates
        "temperature": 0.2,            # near-deterministic extraction
        "top_p":       0.8,
        "top_k":       20,
        "repeat_penalty": 1.05,
    },
)
```

### Pass B — EXTRACT (`llm_extract`, per section)
```python
ollama.chat(
    model="qwen3:1.7b",
    messages=[{"role": "system", "content": EXTRACT_SYSTEM},
              {"role": "user",   "content": extract_user}],
    format="json",
    think=False,
    options={
        "num_ctx":     8192,           # one section is small; comfortable
        "num_predict": 1536,           # claims+entities+triples JSON; never truncate
        "temperature": 0.2,
        "top_p":       0.8,
        "top_k":       20,
        "repeat_penalty": 1.05,
    },
)
```

**Rationale:**
- `num_ctx` set explicitly on every call (fixes silent input truncation, §1d).
- `num_predict` large enough that JSON always completes; with thinking OFF and JSON
  mode, output stays compact so the cap is never actually reached (fixes §1a).
- Non-thinking Qwen3 sampling: temp 0.7/top_p 0.8/top_k 20 is the official
  recommendation; we lower temp to **0.2** because this is deterministic extraction, not
  creative generation. Avoid greedy decoding (temp 0) per Qwen guidance.
- `format="json"` retained for schema reliability (works with thinking OFF).

---

## 6. New `knowledge/topics/{slug}.md` format

Assembled **deterministically** — no LLM call. Pull from the `facts` collection every
claim whose `topic` metadata equals this topic, grouped by source, with section context.

```markdown
# Lead Management Efficiency

> AI lead follow-up cuts response time from 42 hours to under 5 minutes.

## Claims
- AI follow-up reduces lead response time from 42 hours to under 5 minutes. — [the_lazy_way_i_make_money_with_ai_2026.txt § "Plugging the lead-response hole"]
- Faster follow-up directly raises conversion rate because leads go cold within minutes. — [the_lazy_way_i_make_money_with_ai_2026.txt § "Plugging the lead-response hole"]

## Relationships
- AI follow-up system → reduces → lead response time
- Lead response time → drives → conversion rate

---
_Topic appears in 1 source · 2 claims · 2 relationships_
_Sources: the_lazy_way_i_make_money_with_ai_2026.txt_
```

Format rules:
- **H1** = topic (Title Case).
- **Blockquote** (one line) = the most representative claim, used as the graph node
  `description`. Picked deterministically as the **shortest claim ≥ 8 words**, or the
  first claim. (Keeps `export_graph.py:146` first-paragraph read working — see §7.)
- **## Claims** = verbatim extracted claims, each with inline citation
  `[source § "section title"]`. No prose, no re-explanation.
- **## Relationships** = the triples (`subject → predicate → object`).
- **Footer** = counts + source list (machine-friendly, drives the index).
- A topic .md is only written if it has ≥1 claim (no empty stub nodes).

---

## 7. Schema changes

### ChromaDB collections

Keep two collections (names unchanged for compatibility): `chunks` and `facts`.

**`chunks` collection — now stores SECTIONS, not 400-word windows:**
```python
ids:        f"{name}__s{section_idx}"
documents:  section_text
metadatas:  {
  "source": name,
  "section_idx": i,
  "section_title": title,
  "premise": premise,
  "conclusion": conclusion,
}
```

**`facts` collection — now stores atomic CLAIMS with richer metadata:**
```python
ids:        f"{name}__s{section_idx}__c{claim_idx}"
documents:  claim_text
metadatas:  {
  "source": name,
  "section_idx": i,
  "section_title": title,
  "topic": canonical_topic,        # single canonical topic, NOT json list
  "triples": json.dumps([...]),    # triples for this claim's section
}
```

> Change vs. today: `facts` metadata `topics` (a JSON list, `process.py:367`) becomes a
> single `topic` string. This is what lets §6 query `where={"topic": T}` cleanly and
> what bounds topic count. Connection-detection (`process.py:337-358`) is unaffected.

### `sources.json`

Add the structured outline so the graph/index can show video structure:
```json
{
  "the_lazy_way_i_make_money_with_ai_2026.txt": {
    "processed_at": "...",
    "video_summary": "<one-sentence summary from Pass A>",
    "section_count": 7,
    "claim_count": 38,
    "entity_count": 22,
    "topics": ["AI Agency", "Lead Follow-up", "Niche Selection", "..."],
    "sections": [
      {"title": "...", "premise": "...", "conclusion": "..."}
    ]
  }
}
```
> `fact_count`/`chunk_count` keys are renamed to `claim_count`/`section_count`. Keep
> `topics` (list) so `export_graph.py` edge logic (`export_graph.py:121-129`) is
> unchanged. **Update `export_graph.py:160` and `rebuild_index()` in process.py
> (`process.py:271-277`) to read the new key names** (or have them fall back:
> `meta.get("claim_count", meta.get("fact_count", 0))`).

### `connections.json`

No schema change. Connection detection logic is reused as-is, operating on the new
claim embeddings.

### `export_graph.py` reader tolerance (small edits)

- `export_graph.py:146` reads first paragraph as node description. New format's first
  paragraph after the H1 is the **blockquote** — `read_topic_md` already strips the H1
  and takes `paras[0]`; the blockquote `> ...` line becomes that paragraph. Strip a
  leading `> ` in `read_topic_md`. One-line change.
- `export_graph.py:160` source-node description uses `fact_count` → change to
  `claim_count` with fallback.

---

## 8. Step-by-step implementation checklist (for the main agent)

1. **Constants** (`process.py:37-45`): remove `CHUNK_WORDS`/`OVERLAP_WORDS` sliding-window
   use (keep a `MAX_CTX` const). Add `SEGMENT_*` / `EXTRACT_*` option dicts.
2. **Delete** `chunk_text()` (`process.py:72-93`) — replaced by section splitting.
   Keep a small `split_into_sections(text, outline)` helper that splits on
   `start_marker` with equal-slice fallback.
3. **Replace prompts** (`process.py:100-133`): add `SEGMENT_SYSTEM`,
   `SEGMENT_TMPL`, `EXTRACT_SYSTEM`, `EXTRACT_TMPL` from §3. Delete `_SYNTH_TMPL`.
4. **Add `llm_segment(full_text)`** → returns `{video_summary, topics, sections}` with
   the Pass A options from §5; `think=False`; retry/JSON-guard like existing
   `llm_extract` (`process.py:140-162`); strip `<think>` as backstop.
5. **Rewrite `llm_extract(section_text, section_title, topic_list)`** → returns
   `{claims:[{claim,topic}], entities, triples}` with Pass B options from §5. Validate
   each claim's `topic` is in `topic_list`; drop/clamp to nearest if not.
6. **Delete `llm_synthesize()`** (`process.py:165-179`).
7. **Rewrite `update_topic_file(topic, facts_col)`** (`process.py:234-253`) → no LLM;
   query `facts` with `where={"topic": topic}` (or by embedding + filter), assemble the
   §6 markdown deterministically (claims + triples + footer). Skip if 0 claims.
8. **Rewrite `process_transcript()` core loop** (`process.py:303-385`):
   - read full text; call `llm_segment`.
   - `topics = outline["topics"]` (the bounded canonical set — the ONLY topics used).
   - split into sections; for each section: upsert section into `chunks` col with
     premise/conclusion metadata; call `llm_extract`; for each claim embed + upsert into
     `facts` col with single `topic` metadata + triples; run existing connection
     detection (`process.py:337-358`) unchanged.
   - after loop: for each topic in `topics`, `update_topic_file(topic, facts_col)`.
   - write new `sources.json` record (§7), `rebuild_index()`.
9. **Set `num_ctx`/`think`** on every `ollama.chat` call (§5). If the installed `ollama`
   package rejects `think=`, fall back to `/no_think` prefix (wrap in try/except once at
   module load to detect support).
10. **Update `rebuild_index()`** (`process.py:271-277`) and **`export_graph.py`**
    (`:146`, `:160`) for the renamed keys + blockquote stripping (§7).
11. **Verify model context** is enough: transcript ≈ 9K tokens ≤ `num_ctx 16384`. OK.
12. **Test:** `python3 process.py --rebuild` on the existing transcript. Expected:
    **5–8 sections, 4–8 topics, ~25–45 claims**, every topic .md is a tight bulleted
    list of specific claims with section citations, no mid-sentence truncation, no
    generic textbook prose. Then `python3 export_graph.py` and confirm the graph builds
    with the bounded node set.
13. **Sanity asserts** to add: after `llm_segment`, assert `4 <= len(topics) <= 10` (log
    + clamp if violated); assert section count `5 <= n <= 10` (clamp). These guard
    against regression to topic explosion.

---

## 9. Quick reference — param table

| Call        | num_ctx | num_predict | temp | top_p | top_k | think | format |
|-------------|---------|-------------|------|-------|-------|-------|--------|
| SEGMENT (A) | 16384   | 2048        | 0.2  | 0.8   | 20    | False | json   |
| EXTRACT (B) | 8192    | 1536        | 0.2  | 0.8   | 20    | False | json   |
| (synthesis) | —       | —           | —    | —     | —     | DELETED       |

---

## Sources

- Ollama num_ctx / silent input truncation: https://docs.ollama.com/faq , https://www.serverman.co.uk/ai/ollama/ollama-context-window/
- Ollama num_predict (-1 = unlimited, mid-sentence cutoff): https://github.com/ollama/ollama/issues/7691 , https://technovangelist.com/notes/num_predict
- Qwen3-1.7B thinking control + sampling params + structured-output caveat: https://huggingface.co/Qwen/Qwen3-1.7B , https://medium.com/@maganuriyev/ollama-on-cpu-qwen3-with-reasoning-structured-output-to-solve-any-nlp-problem-4e6d5bd2b7a7
- Atomic claim / triple extraction (extractive vs abstractive): https://arxiv.org/pdf/2604.02866 , https://medium.com/@EleventhHourEnthusiast/zero-and-few-shots-knowledge-graph-triplet-extraction-with-large-language-models-cf571eb7fc98
