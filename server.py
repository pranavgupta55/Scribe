#!/usr/bin/env python3
"""
Scribe local server.

Serves the static graph viewer AND a RAG chat endpoint over the knowledge base.

  GET  /<anything>   → static files (graph viewer, etc.)
  GET  /api/status   → JSON Gemini/qwen backend status
  POST /api/chat     → Server-Sent Events stream:
                         {"type":"nodes","nodes":[...]}   topics consulted
                         {"type":"debug",...}             system/context/prompt
                         {"type":"backend","backend":"gemini"|"qwen"}  which engine
                         {"type":"token","text":"..."}    streamed answer
                         {"type":"done"}                  end of turn
                         {"type":"notice",...}            non-fatal status note
                         {"type":"error","message":"..."} failure

Retrieval is grounded entirely in the local ChromaDB collections produced by
process.py (`facts` for precise claims + consulted-topic surfacing, `chunks`
for full-context passages). Retrieval (query embedding + vector search) stays
fully local via Ollama `nomic-embed-text`; only generation is delegated to
Google Gemini (free tier) via the `google-genai` SDK.
"""

import json
import os
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CHROMA_DIR  = SCRIPT_DIR / ".chroma"
PORT        = 8765

EMBED_MODEL   = "nomic-embed-text"
# Gemini generation models (primary, then fallback if the primary is unavailable).
GEMINI_MODEL          = "gemini-2.5-flash"
GEMINI_MODEL_FALLBACK = "gemini-2.0-flash"
GEMINI_API_KEY_ENV    = "GEMINI_API_KEY"
# Local fallback model — used when Gemini is rate-limited / unavailable so the
# chat always answers instead of erroring out.
QWEN_MODEL    = "qwen3:1.7b"

N_FACTS  = 10    # facts retrieved (precise grounding + consulted-topic surfacing)
N_CHUNKS = 5     # full-context passages for the answer
MAX_TOPICS = 8
# Copy-paste RAG mode: wider net since the output goes to a larger external model
RAG_N_FACTS  = 50
RAG_N_CHUNKS = 50
RAG_MAX_TOPICS = 24

_SYSTEM = """\
You are Scribe, a personal-knowledge-base assistant. You answer questions by
reasoning over passages and facts retrieved from the user's own notes and
transcripts. Every factual claim you make should be traceable to the retrieved
context shown to you.

<retrieval_grounded_behavior>
You are given retrieved passages and key facts before each question. These are
the PRIMARY and AUTHORITATIVE source for your answer.

1. ANCHOR every concrete claim to at least one retrieved passage or fact. If a
   claim cannot be traced to the context, mark it as your own inference using
   the rules in <uncertainty_and_inference> below.
2. USE RELATED CONTEXT. If passages address the question tangentially,
   partially, or by strong implication, USE them. Do NOT refuse just because
   the exact wording of the question doesn't appear verbatim. Reason from
   what is present.
3. SYNTHESIZE across passages freely when that produces a more complete
   answer. You are not required to quote directly — paraphrase and integrate
   naturally. Reserve direct quotation for cases where exact wording matters
   (definitions, named values, specific instructions).
4. PRIORITIZE USEFULNESS. A partial, hedged, context-grounded answer is
   almost always more valuable than "I don't have that in my knowledge base."
   Default toward answering. Reserve refusal for the rare zero-signal case.
</retrieval_grounded_behavior>

<citation_rules>
- Source files: cite inline as the bare filename, e.g. (100m_offers.txt).
  Do not prefix with "Source:" or "From:". For multiple sources backing one
  claim, list them together: (file_a.txt, file_b.txt).
- Topic links: cite extracted topic names in [[double brackets]], e.g.
  [[grand slam offer]], so the UI can render them as clickable links.
  Use these only when the topic adds navigation value — don't link every noun.
- One citation per claim is enough. Don't repeat the same citation in
  consecutive sentences unless a different passage is being used.
- Don't manufacture source names. If a passage has no clear filename in the
  context, omit the citation instead of inventing one.
</citation_rules>

<synthesis_vs_quoting>
Default to synthesis — fluent prose that integrates multiple passages into a
coherent answer. Use direct quotation only when:
- the user asks "what exactly does X say about Y";
- a specific phrase, formula, definition, or instruction must be reproduced
  verbatim;
- paraphrase risks distorting meaning.

Connecting ideas stated separately is acceptable when the connection is a
reasonable inference from the combined text — but flag the connection per
<uncertainty_and_inference>.
</synthesis_vs_quoting>

<uncertainty_and_inference>
Calibrate your confidence language to how directly the context supports each
claim:

- DIRECTLY SUPPORTED: state the claim plainly with a citation. No hedge.
- PARTIALLY / ADJACENT: answer, then flag the inferential step
  ("though this isn't stated explicitly", "based on what's here").
- SYNTHESIZED across passages: state the synthesis, then acknowledge it
  ("combining the two ideas:", "my reading of the combined context").
- WEAK SIGNAL: if context is only loosely related, say so and answer
  cautiously ("the retrieved context covers a related topic but doesn't
  directly address your question — based on what's here:").

Never state invented facts confidently. If you reason beyond the context,
mark it: "My inference:" or "Not in the retrieved passages, but generally:".
</uncertainty_and_inference>

<when_to_decline>
Say "I don't have that in my knowledge base" ONLY when:
1. Retrieved passages have zero semantic overlap with the question, AND
2. You can't form even a partial, hedged answer from tangential content.

This is a HIGH BAR. When in doubt, answer with hedging instead of declining.
Never use refusal as a shortcut to skip reasoning over adjacent content.
</when_to_decline>

<tone_and_format>
- Write in direct, conversational prose. Skip preambles ("Great question!",
  "Based on your notes..."). Start with the answer.
- Match length to complexity. Simple factual: 2-4 sentences. Conceptual: 1-3
  short paragraphs. Don't pad.
- Use bullet lists only when the question asks for one or the answer is
  genuinely a list.
- Inline citations and [[topic links]] only — no separate "Sources:" footer
  unless the user asks for one.
- If passages conflict, surface the conflict instead of silently picking one.
</tone_and_format>"""

# ── Module-level Gemini backend state ────────────────────────────────────────
# Tracks whether Gemini is in a rate-limit cooldown so the UI can show a live
# countdown.  All fields are written under the GIL (CPython) — no explicit lock
# needed for simple reads/writes.

_gemini_cooldown_until: float = 0.0   # epoch seconds; 0 means not in cooldown
_gemini_retry_known: bool = False      # True when we parsed a real retry delay
_gemini_last_backend: str | None = None  # "gemini" | "qwen" | None
_gemini_last_success_ts: float | None = None   # epoch time of last successful Gemini call
_gemini_call_timestamps: list = []             # recent call epoch times (rolling, last 90s)


def _parse_retry_seconds(exc_str: str) -> int | None:
    """Try to extract a retry delay (seconds) from a Gemini error message.

    Google returns retryDelay in protobuf/JSON form, e.g.:
        retryDelay: "30s"
        "retryDelay":"60s"
        retry in 30s
    Returns None if no delay can be parsed.
    """
    # Pattern 1: retryDelay":"30s" or retryDelay: "30s" or retryDelay=30s
    m = re.search(r'retryDelay"?\s*[:=]\s*"?(\d+)s', exc_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Pattern 2: "retry in 30s" / "retry in 30 seconds"
    m = re.search(r'retry\s+in\s+(\d+)', exc_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _record_gemini_success():
    global _gemini_cooldown_until, _gemini_retry_known, _gemini_last_backend
    global _gemini_last_success_ts, _gemini_call_timestamps
    _gemini_cooldown_until = 0.0
    _gemini_retry_known = False
    _gemini_last_backend = "gemini"
    now = time.time()
    _gemini_last_success_ts = now
    _gemini_call_timestamps.append(now)
    # Keep only last 90s
    _gemini_call_timestamps = [t for t in _gemini_call_timestamps if now - t <= 90]


def _record_gemini_ratelimit(exc_str: str):
    global _gemini_cooldown_until, _gemini_retry_known, _gemini_last_backend
    delay = _parse_retry_seconds(exc_str)
    if delay is not None:
        _gemini_cooldown_until = time.time() + delay
        _gemini_retry_known = True
    else:
        # Daily quota or unknown — don't fabricate a timer
        _gemini_cooldown_until = time.time() + 1   # just marks "in cooldown"
        _gemini_retry_known = False
    _gemini_last_backend = "qwen"


def _record_qwen_used():
    global _gemini_last_backend
    _gemini_last_backend = "qwen"


def _gemini_status() -> dict:
    """Return the dict emitted by GET /api/status."""
    has_key = bool(os.environ.get(GEMINI_API_KEY_ENV))
    now = time.time()
    in_cooldown = _gemini_cooldown_until > now
    remaining: int | None = None
    if in_cooldown and _gemini_retry_known:
        remaining = max(0, int(_gemini_cooldown_until - now))
    gemini_ok = has_key and not in_cooldown
    return {
        "has_key": has_key,
        "gemini_ok": gemini_ok,
        "in_cooldown": in_cooldown,
        "cooldown_remaining": remaining,
        "retry_known": _gemini_retry_known,
        "last_backend": _gemini_last_backend,
        "last_success_ts": _gemini_last_success_ts,
        "recent_calls_60s": len([t for t in _gemini_call_timestamps if time.time() - t <= 60]),
    }


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _topic_display(topic):
    """Match the graph node id produced by export_graph.py (slug → Title Case)."""
    return _slug(topic).replace("_", " ").title()


_DECOMPOSE_SYSTEM = """\
<instructions>
Split the user question into atomic sub-questions if it covers multiple distinct topics.
An atomic sub-question covers exactly one topic or asks one thing.
If the input already asks about one thing, output it unchanged.
Causal questions like "what impact did X have on Y" must be split into the factual components (e.g. "What is X?" and "What is Y?") so each can be looked up independently.
Output ONLY the sub-questions, one per line, inside <output> tags. No numbering, no explanation, no other text.
Maximum 5 sub-questions.
</instructions>

<examples>
<example>
<input>What are Alex's thoughts on sales funnels and how does he recommend structuring an offer?</input>
<output>
What are Alex's thoughts on sales funnels?
How does Alex recommend structuring an offer?
</output>
</example>
<example>
<input>Tell me about Alex Hormozi's background and his early business failures.</input>
<output>
What is Alex Hormozi's background?
What were Alex Hormozi's early business failures?
</output>
</example>
<example>
<input>I want to understand Alex's strategy for acquiring businesses and his philosophy on team building.</input>
<output>
What is Alex's strategy for acquiring businesses?
What is Alex's philosophy on team building?
</output>
</example>
<example>
<input>How does Alex approach marketing for a new product, and what metrics does he track for success?</input>
<output>
How does Alex approach marketing for a new product?
What metrics does Alex track for marketing success?
</output>
</example>
<example>
<input>What's the difference between lead generation and customer retention in Alex's view, and which does he prioritize?</input>
<output>
What is the difference between lead generation and customer retention in Alex's view?
Which does Alex prioritize: lead generation or customer retention?
</output>
</example>
<example>
<input>Alex's advice on pricing strategies and getting customers to pay more.</input>
<output>
What is Alex's advice on pricing strategies?
How does Alex advise getting customers to pay more?
</output>
</example>
<example>
<input>What are Alex's key principles for scaling a business and delegating tasks effectively?</input>
<output>
What are Alex's key principles for scaling a business?
What are Alex's key principles for delegating tasks effectively?
</output>
</example>
<example>
<input>Can you explain Alex's concept of Grand Slam Offers and how it relates to value delivery?</input>
<output>
What is Alex's concept of Grand Slam Offers?
How does Alex's concept of Grand Slam Offers relate to value delivery?
</output>
</example>
<example>
<input>Hormozi's thoughts on building a strong company culture and motivating employees.</input>
<output>
What are Alex Hormozi's thoughts on building a strong company culture?
What are Alex Hormozi's thoughts on motivating employees?
</output>
</example>
<example>
<input>What's Alex's take on venture capital funding versus bootstrapping, and which path does he typically recommend?</input>
<output>
What is Alex's take on venture capital funding versus bootstrapping?
Which path does Alex typically recommend: venture capital funding or bootstrapping?
</output>
</example>
<example>
<input>What's Alex Hormozi's definition of a "Grand Slam Offer"?</input>
<output>
What's Alex Hormozi's definition of a "Grand Slam Offer"?
</output>
</example>
<example>
<input>How does Alex recommend structuring a compensation plan for sales staff?</input>
<output>
How does Alex recommend structuring a compensation plan for sales staff?
</output>
</example>
<example>
<input>What are Alex's top 3 books he recommends for entrepreneurs?</input>
<output>
What are Alex's top 3 books he recommends for entrepreneurs?
</output>
</example>
<example>
<input>Tell me about Alex Hormozi's experience with the gym industry.</input>
<output>
Tell me about Alex Hormozi's experience with the gym industry.
</output>
</example>
<example>
<input>What is Alex's perspective on the importance of lead generation?</input>
<output>
What is Alex's perspective on the importance of lead generation?
</output>
</example>
<example>
<input>How does Alex's approach to lead generation differ from traditional marketing methods, and why does he prefer his method?</input>
<output>
How does Alex's approach to lead generation differ from traditional marketing methods?
Why does Alex prefer his lead generation method?
</output>
</example>
<example>
<input>What impact did Alex's early failures have on his current business philosophy?</input>
<output>
What are Alex's early business failures?
What is Alex's current business philosophy?
</output>
</example>
<example>
<input>If an entrepreneur implements Alex's advice on offer creation, what results can they expect to see in their business?</input>
<output>
If an entrepreneur implements Alex's advice on offer creation, what results can they expect to see in their business?
</output>
</example>
<example>
<input>What are the pros and cons of using Alex's "Grand Slam Offer" framework, and who is it best suited for?</input>
<output>
What are the pros of using Alex's "Grand Slam Offer" framework?
What are the cons of using Alex's "Grand Slam Offer" framework?
Who is Alex's "Grand Slam Offer" framework best suited for?
</output>
</example>
<example>
<input>Alex's thoughts on creating urgency in sales. Is it ethical, and does he use specific tactics?</input>
<output>
What are Alex's thoughts on creating urgency in sales?
Is creating urgency in sales ethical according to Alex?
Does Alex use specific tactics for creating urgency in sales?
</output>
</example>
</examples>"""


def _decompose_query(query: str) -> list[str]:
    """Split a multi-topic query into atomic sub-queries using local qwen3:1.7b.
    Falls back to Gemini, then to [query] on any error."""
    import re as _re
    import ollama as _ollama

    def _parse(text: str) -> list[str] | None:
        # Strip any residual <think>...</think> blocks before parsing
        text = _re.sub(r'<think>.*?</think>', '', text, flags=_re.DOTALL)
        m = _re.search(r'<output>\s*(.*?)\s*</output>', text, _re.DOTALL)
        raw = m.group(1) if m else text
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        # Drop any stray xml-ish lines
        lines = [l for l in lines if not l.startswith('<') and not l.startswith('>')]
        # Trim common trailing junk (markdown bullets, numbering)
        lines = [_re.sub(r'^[\d]+[\.\)]\s*|^[-*]\s+', '', l).strip() for l in lines]
        lines = [l for l in lines if l]
        if 1 <= len(lines) <= 5:
            return lines
        return None

    # Try qwen3 first (local, free, no rate limit).
    # think=True: qwen3 reasons in `thinking` field, returns clean answer in `content`.
    # Quality > speed here — decomp drives all retrieval, payload is small (single query).
    try:
        resp = _ollama.chat(
            model=QWEN_MODEL,
            messages=[
                {"role": "system", "content": _DECOMPOSE_SYSTEM},
                {"role": "user",   "content": f"<input>{query}</input>"},
            ],
            think=True,
            options={"num_predict": 800, "temperature": 0},
        )
        result = _parse(resp["message"]["content"])
        if result:
            return result
    except Exception:
        pass

    # Gemini fallback
    try:
        client = _gemini_client()
        from google.genai import types as _types
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=_DECOMPOSE_SYSTEM + f"\n\n<input>{query}</input>",
            config=_types.GenerateContentConfig(
                temperature=0, max_output_tokens=200,
                thinking_config=_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        result = _parse(resp.text)
        if result:
            return result
    except Exception:
        pass

    return [query]


def _merge_chroma_results(results_list, cap):
    """Merge ChromaDB results across sub-queries. Returns (docs, metas, query_indices_per_doc).
    query_indices_per_doc[i] = sorted list of sub-query indices that retrieved doc i."""
    seen = {}  # text -> [doc, meta, hits, set_of_sq_indices]
    for sq_idx, (docs, metas) in enumerate(results_list):
        for doc, meta in zip(docs, metas):
            if doc not in seen:
                seen[doc] = [doc, meta, 0, set()]
            seen[doc][2] += 1
            seen[doc][3].add(sq_idx)
    ranked = sorted(seen.values(), key=lambda x: -x[2])
    docs    = [x[0] for x in ranked[:cap]]
    metas   = [x[1] for x in ranked[:cap]]
    indices = [sorted(x[3]) for x in ranked[:cap]]
    return docs, metas, indices


def retrieve_structured(query, n_facts=RAG_N_FACTS, n_chunks=RAG_N_CHUNKS,
                        max_topics=RAG_MAX_TOPICS):
    """Wider RAG used by the copy-paste view. Returns
        {topics: [...], sources: [{name, passages: [{section_title, text}], facts: [...]}]}
    grouped by source filename, in retrieval-relevance order.
    """
    import chromadb, ollama
    from collections import OrderedDict

    sub_queries = _decompose_query(query)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts_col  = client.get_collection("facts")
    chunks_col = client.get_collection("chunks")

    def _q(col, n, emb):
        c = col.count()
        if c == 0:
            return {"documents": [[]], "metadatas": [[]]}
        return col.query(query_embeddings=[emb], n_results=min(n, c))

    fact_results, chunk_results = [], []
    for sq in sub_queries:
        emb = ollama.embeddings(model=EMBED_MODEL, prompt=sq)["embedding"]
        fr = _q(facts_col, n_facts, emb)
        cr = _q(chunks_col, n_chunks, emb)
        fact_results.append((fr["documents"][0], fr["metadatas"][0]))
        chunk_results.append((cr["documents"][0], cr["metadatas"][0]))

    fact_docs, fact_metas, fact_qidxs     = _merge_chroma_results(fact_results, n_facts)
    chunk_docs, chunk_metas, chunk_qidxs = _merge_chroma_results(chunk_results, n_chunks)

    topics, seen = [], set()
    for meta in fact_metas:
        t = meta.get("topic")
        if not t:
            continue
        disp = _topic_display(t)
        if disp and disp.lower() not in seen:
            seen.add(disp.lower())
            topics.append(disp)
    topics = topics[:max_topics]

    # Track which sub-queries contributed to each source (for frontend grouping).
    from collections import Counter
    src_qi_hits = {}  # source_name -> Counter of sub-query indices
    for meta, qidxs in zip(chunk_metas, chunk_qidxs):
        s = meta.get("source", "?")
        src_qi_hits.setdefault(s, Counter())
        for qi in qidxs:
            src_qi_hits[s][qi] += 1
    for meta, qidxs in zip(fact_metas, fact_qidxs):
        s = meta.get("source", "?")
        src_qi_hits.setdefault(s, Counter())
        for qi in qidxs:
            src_qi_hits[s][qi] += 1

    by_src = OrderedDict()
    for doc, meta in zip(chunk_docs, chunk_metas):
        s = meta.get("source", "?")
        by_src.setdefault(s, {"passages": [], "facts": []})
        by_src[s]["passages"].append({
            "section_title": meta.get("section_title", ""),
            "text": doc,
        })
    for doc, meta in zip(fact_docs, fact_metas):
        s = meta.get("source", "?")
        by_src.setdefault(s, {"passages": [], "facts": []})
        by_src[s]["facts"].append(doc)

    try:
        src_meta = json.loads((SCRIPT_DIR / "knowledge" / "sources.json").read_text())
    except Exception:
        src_meta = {}
    sources = []
    for s, blk in by_src.items():
        meta = src_meta.get(s, {})
        hits = src_qi_hits.get(s, Counter())
        primary_idx = hits.most_common(1)[0][0] if hits else 0
        sources.append({
            "name": s,
            "title": meta.get("title", ""),
            "video_summary": meta.get("video_summary", ""),
            "url": meta.get("url", ""),
            "primary_query_idx": primary_idx,
            "query_indices": sorted(hits.keys()),
            **blk,
        })
    # Sort sources by primary sub-query so frontend groups are contiguous.
    sources.sort(key=lambda s: s["primary_query_idx"])
    return {"topics": topics, "sources": sources, "sub_queries": sub_queries}


def retrieve(query, seen_sources=None):
    """Return (consulted_topic_names, context_block, sub_queries, source_names).
    `seen_sources`: iterable of source filenames already attached to prior turns
    in the conversation — those are filtered OUT of this turn's context block
    so we don't waste tokens re-injecting them. The dedup is per-turn, not
    per-passage — once a source has been seen, the model already has it via
    earlier history and only needs the new sources fresh."""
    import chromadb, ollama

    seen = set(seen_sources or [])
    sub_queries = _decompose_query(query)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts_col  = client.get_collection("facts")
    chunks_col = client.get_collection("chunks")

    def _query(col, n, emb):
        count = col.count()
        if count == 0:
            return {"documents": [[]], "metadatas": [[]]}
        return col.query(query_embeddings=[emb], n_results=min(n, count))

    fact_results, chunk_results = [], []
    for sq in sub_queries:
        emb = ollama.embeddings(model=EMBED_MODEL, prompt=sq)["embedding"]
        fr = _query(facts_col, N_FACTS, emb)
        cr = _query(chunks_col, N_CHUNKS, emb)
        fact_results.append((fr["documents"][0], fr["metadatas"][0]))
        chunk_results.append((cr["documents"][0], cr["metadatas"][0]))

    fact_docs,  fact_metas,  _ = _merge_chroma_results(fact_results,  N_FACTS)
    chunk_docs, chunk_metas, _ = _merge_chroma_results(chunk_results, N_CHUNKS)

    # Consulted topics, in relevance order, deduped → graph node ids.
    # facts metadata carries a single canonical `topic` (see process.py schema).
    topics, _topic_seen = [], set()
    for meta in fact_metas:
        t = meta.get("topic")
        if not t:
            continue
        disp = _topic_display(t)
        if disp and disp.lower() not in _topic_seen:
            _topic_seen.add(disp.lower())
            topics.append(disp)
    topics = topics[:MAX_TOPICS]

    # Context block — grouped BY SOURCE so the model (and the Dev panel) sees a
    # clear boundary between videos instead of one undifferentiated wall of text.
    from collections import OrderedDict
    by_src = OrderedDict()
    for doc, meta in zip(chunk_docs, chunk_metas):
        s = meta.get("source", "?")
        by_src.setdefault(s, {"passages": [], "facts": []})
        by_src[s]["passages"].append((meta.get("section_title", ""), doc))
    for doc, meta in zip(fact_docs, fact_metas):
        s = meta.get("source", "?")
        by_src.setdefault(s, {"passages": [], "facts": []})
        by_src[s]["facts"].append(doc)

    parts = []
    used_sources = []
    new_idx = 0
    for s, blk in by_src.items():
        if s in seen:
            continue  # already attached in a prior turn; rely on history
        new_idx += 1
        parts.append(f"===== SOURCE {new_idx}: {s} =====")
        used_sources.append(s)
        if blk["passages"]:
            parts.append("Passages:")
            for title, doc in blk["passages"]:
                hdr = f"[§ {title}] " if title else ""
                parts.append(f"{hdr}{doc}")
        if blk["facts"]:
            parts.append("\nKey facts from this source:")
            parts.extend(f"- {f}" for f in blk["facts"])
        parts.append("")  # blank line between sources

    return topics, "\n".join(parts).strip(), sub_queries, used_sources


def _gemini_client():
    """Build a Gemini client. Raises RuntimeError with a user-facing message
    if the API key is missing, ImportError if the SDK isn't installed."""
    api_key = os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{GEMINI_API_KEY_ENV} is not set. Get a free key at "
            f"https://aistudio.google.com/apikey then run: "
            f"export {GEMINI_API_KEY_ENV}=your_key_here")
    from google import genai
    return genai.Client(api_key=api_key)


def build_prompt(query, context):
    """The exact user prompt sent to Gemini (also surfaced in the Dev panel)."""
    if not context:
        return f"Question: {query}\n\nAnswer:"
    return (f"Context from the knowledge base (sources new this turn):\n\n{context}\n\n"
            f"Question: {query}\n\nAnswer:")


# ── Chat history + tiered token-budget compaction ────────────────────────────
# Layer the conversation onto Gemini's 32k context window. Tier 1 fires at
# 75% (24k): compress oldest assistant responses to a short truncation.
# Tier 2 fires at 100% (32k): drop oldest entire turns from the wire context.
# User messages are preserved verbatim until Tier 2 evicts the whole turn —
# their intent signal is small and irreplaceable.

CONTEXT_WINDOW   = 32_000
TIER_1_THRESHOLD = 24_000   # 75% — compress oldest assistant content
TIER_2_THRESHOLD = 32_000   # 100% — drop oldest entire turns

def _approx_tokens(s: str) -> int:
    # GPT-style BPE averages ~3.5 chars/token on English prose. Good enough
    # for budget planning here — we're not metering, we're triggering tiers.
    return max(1, (len(s) if s else 0) // 4)


def build_chat_entries(history, query, context):
    """Build a list of {role: 'user'|'assistant', content: str} entries
    for the conversation, applying tiered compaction so the wire payload
    fits Gemini's 32k window. The current question (with this-turn context
    block) is appended at the end. Returns (entries, total_tokens)."""
    reserved = _approx_tokens(_SYSTEM) + 800  # response headroom

    entries = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content") or ""
        if role not in ("user", "assistant") or not content:
            continue
        entries.append({
            "role": role,
            "content": content,
            "tokens": _approx_tokens(content),
            "compressed": bool(turn.get("compressed", False)),
        })

    # Final turn: this question + its context block. Context is only the NEW
    # (un-seen) sources — older sources came in via prior turns' messages.
    current_text = build_prompt(query, context)
    entries.append({
        "role": "user",
        "content": current_text,
        "tokens": _approx_tokens(current_text),
        "compressed": False,
    })

    total = sum(e["tokens"] for e in entries) + reserved

    # Tier 1 — compress oldest assistant turns to a short summary line.
    if total > TIER_1_THRESHOLD:
        for e in entries[:-1]:  # never touch the current question
            if e["role"] == "assistant" and not e["compressed"]:
                # Naive truncation; matches the architecture-doc compaction
                # contract (assistant content is reducible, user msgs aren't).
                trimmed = e["content"][:400].rstrip()
                if len(e["content"]) > 400:
                    trimmed += " […compacted]"
                new_tok = _approx_tokens(trimmed)
                if new_tok < e["tokens"]:
                    total -= (e["tokens"] - new_tok)
                    e["content"] = trimmed
                    e["tokens"] = new_tok
                    e["compressed"] = True
            if total <= TIER_1_THRESHOLD:
                break

    # Tier 2 — drop oldest entire turns until we fit. Keep the most recent
    # exchange + current question intact at minimum.
    while total > TIER_2_THRESHOLD and len(entries) > 2:
        oldest = entries.pop(0)
        total -= oldest["tokens"]

    return entries, total


def _entries_to_gemini_contents(entries):
    """Gemini's `contents` schema uses role 'model' for assistant turns."""
    return [
        {"role": "user" if e["role"] == "user" else "model",
         "parts": [{"text": e["content"]}]}
        for e in entries
    ]


def generate_stream(client, entries):
    """Yield generated text chunks from Gemini, trying the primary model then
    the fallback. `entries` is the compacted message history (list of
    {role, content, ...}). Raises on hard failure (after fallback also fails)."""
    from google.genai import types

    contents = _entries_to_gemini_contents(entries)

    last_err = None
    for model in (GEMINI_MODEL, GEMINI_MODEL_FALLBACK):
        try:
            kwargs = dict(system_instruction=_SYSTEM, temperature=0.2,
                          max_output_tokens=2048)
            # Disable "thinking" on 2.5-flash — otherwise it can spend the entire
            # output budget reasoning and return ZERO visible text (the empty-reply
            # bug). 2.0-flash has no thinking, so leave it untouched.
            if model == GEMINI_MODEL:
                try:
                    kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                except Exception:
                    pass
            config = types.GenerateContentConfig(**kwargs)
            stream = client.models.generate_content_stream(
                model=model, contents=contents, config=config)
            for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
            return  # success
        except Exception as e:  # noqa: BLE001 — try the fallback model next
            last_err = e
            continue
    raise RuntimeError(str(last_err) if last_err else "Gemini generation failed.")


def qwen_stream(entries):
    """Local fallback generation — stream from Ollama qwen3:1.7b. `entries` is
    the compacted message history. Used when Gemini is rate-limited or
    unavailable so the chat still answers."""
    import ollama
    import re as _re
    messages = [{"role": "system", "content": "/no_think\n" + _SYSTEM}]
    for e in entries:
        messages.append({"role": e["role"], "content": e["content"]})
    stream = ollama.chat(
        model=QWEN_MODEL,
        messages=messages,
        stream=True,
        options={"temperature": 0.2, "num_ctx": 32768, "num_predict": 1024},
    )
    for part in stream:
        tok = part.get("message", {}).get("content", "")
        if tok:
            # /no_think keeps reasoning out; strip any stray tags defensively
            yield _re.sub(r"</?think>", "", tok)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SCRIPT_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass  # quiet

    def _sse(self, obj):
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()

    def _json_response(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self._json_response(_gemini_status())
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/rag":
            # Wider retrieval, no LLM — used by the Copy-paste view to surface
            # raw RAG sources for pasting into an external model.
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                query = (body.get("query") or "").strip()
            except json.JSONDecodeError:
                query = ""
            if not query:
                self._json_response({"error": "Empty query."}, status=400)
                return
            try:
                res = retrieve_structured(query)
            except Exception as e:
                self._json_response({"error": f"Retrieval failed: {e}"}, status=500)
                return
            res["query"] = query
            self._json_response(res)
            return

        if self.path != "/api/chat":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            query = (body.get("query") or "").strip()
            gemini_only = bool(body.get("gemini_only", False))
            qwen_only = bool(body.get("qwen_only", False))
            history = body.get("history") or []  # [{role, content, sources?}]
            if not isinstance(history, list):
                history = []
        except json.JSONDecodeError:
            query = ""
            gemini_only = False
            qwen_only = False
            history = []

        # Sources already attached in prior turns — filter them out of this
        # turn's context block (model still has them via prior assistant msgs).
        seen_sources = []
        for h in history:
            for s in (h.get("sources") or []):
                if s not in seen_sources:
                    seen_sources.append(s)

        # Close the connection when the handler returns. Without this the
        # keep-alive socket stays open, the browser's stream reader never
        # resolves `done`, and the chat UI gets stuck after one question.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        if not query:
            self._sse({"type": "error", "message": "Empty query."})
            self._sse({"type": "done"})
            return

        # ── Retrieve ──
        try:
            topics, context, sub_queries, sources_used = retrieve(query, seen_sources=seen_sources)
        except Exception as e:
            self._sse({"type": "error",
                       "message": f"Knowledge base unavailable ({e}). "
                                  f"Is Ollama running and has updateDB.sh been run?"})
            self._sse({"type": "done"})
            return

        self._sse({"type": "nodes", "nodes": topics})
        # Tell the client which source filenames are attached to this turn so
        # it can include them in the history payload on the NEXT request.
        self._sse({"type": "sources", "sources": sources_used})

        # Build the compacted message list (history + current question).
        entries, total_tokens = build_chat_entries(history, query, context)

        # ── Debug round-trip (Dev panel): the exact strings we send ──
        self._sse({"type": "debug",
                   "system": _SYSTEM,
                   "context": context,
                   "prompt": build_prompt(query, context),
                   "sub_queries": sub_queries,
                   "history_tokens": total_tokens,
                   "history_msgs": len(entries)})

        # Empty KB AND no prior context = nothing to answer from. Otherwise we
        # always have at least the history to draw on, so proceed.
        if not context and not history:
            self._sse({"type": "token",
                       "text": "I don't have anything in my knowledge base yet. "
                               "Run `updateDB.sh` to process some transcripts first."})
            self._sse({"type": "done"})
            return

        # ── Generate: prefer Gemini, fall back to local qwen unless gemini_only ──
        # qwen_only: skip Gemini entirely and go straight to local model.
        if qwen_only:
            self._sse({"type": "backend", "backend": "qwen"})
            _record_qwen_used()
            try:
                for tok in qwen_stream(entries):
                    self._sse({"type": "token", "text": tok})
            except BrokenPipeError:
                return
            except Exception as e:
                self._sse({"type": "error", "message": f"Local generation failed: {e}"})
            self._sse({"type": "done"})
            return

        client = None
        try:
            client = _gemini_client()
        except (RuntimeError, ImportError):
            client = None  # no key / SDK → straight to qwen (or error if gemini_only)

        # Check if we're currently in a Gemini rate-limit cooldown.
        now = time.time()
        in_cooldown = _gemini_cooldown_until > now

        if client is None or in_cooldown:
            # Gemini is unavailable or rate-limited right now.
            if gemini_only:
                # Build a user-facing message with the real wait if known.
                if in_cooldown and _gemini_retry_known:
                    remaining = max(0, int(_gemini_cooldown_until - now))
                    msg = f"Gemini is rate-limited. Retry in {remaining}s."
                elif in_cooldown:
                    msg = "Gemini is rate-limited — retry time unknown (Gemini quota exhausted)."
                else:
                    msg = ("Gemini is unavailable (no API key or SDK not installed). "
                           "Disable 'Gemini only' to use the local model.")
                self._sse({"type": "error", "message": msg})
                self._sse({"type": "done"})
                return
            else:
                if in_cooldown:
                    if _gemini_retry_known:
                        remaining = max(0, int(_gemini_cooldown_until - now))
                        note = f"Gemini rate-limited (retry in {remaining}s) — answering with local qwen3:1.7b."
                    else:
                        note = "Gemini rate-limited — retry time unknown (quota exhausted) — answering with local qwen3:1.7b."
                    self._sse({"type": "notice", "text": note})
                self._sse({"type": "backend", "backend": "qwen"})
                _record_qwen_used()
                try:
                    for tok in qwen_stream(entries):
                        self._sse({"type": "token", "text": tok})
                except BrokenPipeError:
                    return
                except Exception as e:
                    self._sse({"type": "error", "message": f"Local generation failed: {e}"})
                self._sse({"type": "done"})
                return

        # Gemini is available — attempt it.
        try:
            yielded = False
            try:
                self._sse({"type": "backend", "backend": "gemini"})
                for tok in generate_stream(client, entries):
                    yielded = True
                    self._sse({"type": "token", "text": tok})
                if yielded:
                    _record_gemini_success()
                    self._sse({"type": "done"})
                    return
                # Gemini returned nothing → fall through to qwen
                _record_gemini_success()  # no error, just empty
            except BrokenPipeError:
                return  # client navigated away
            except Exception as e:  # noqa: BLE001 — rate limit / API error
                if yielded:
                    # partial answer already sent; don't switch mid-stream
                    self._sse({"type": "done"})
                    return
                exc_str = str(e)
                is_ratelimit = "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str
                if is_ratelimit:
                    _record_gemini_ratelimit(exc_str)
                    if gemini_only:
                        now2 = time.time()
                        in_cd = _gemini_cooldown_until > now2
                        if in_cd and _gemini_retry_known:
                            remaining = max(0, int(_gemini_cooldown_until - now2))
                            msg = f"Gemini is rate-limited. Retry in {remaining}s."
                        elif in_cd:
                            msg = "Gemini is rate-limited — retry time unknown (Gemini quota exhausted)."
                        else:
                            msg = "Gemini rate-limited."
                        self._sse({"type": "error", "message": msg})
                        self._sse({"type": "done"})
                        return
                    # Fall back to qwen
                    if _gemini_retry_known:
                        remaining = max(0, int(_gemini_cooldown_until - time.time()))
                        note = f"Gemini rate-limited (retry in {remaining}s) — answering with local qwen3:1.7b."
                    else:
                        note = "Gemini rate-limited — retry time unknown (quota exhausted) — answering with local qwen3:1.7b."
                    self._sse({"type": "notice", "text": note})
                else:
                    if gemini_only:
                        self._sse({"type": "error",
                                   "message": f"Gemini unavailable: {exc_str}"})
                        self._sse({"type": "done"})
                        return
                    self._sse({"type": "notice",
                               "text": f"Gemini unavailable — answering with local qwen3:1.7b."})

            if not gemini_only:
                # Local fallback after Gemini empty/error
                self._sse({"type": "backend", "backend": "qwen"})
                _record_qwen_used()
                for tok in qwen_stream(entries):
                    self._sse({"type": "token", "text": tok})
        except BrokenPipeError:
            return
        except Exception as e:
            self._sse({"type": "error", "message": f"Generation failed: {e}"})

        self._sse({"type": "done"})


def main():
    httpd = ThreadingHTTPServer(("", PORT), Handler)
    print(f"🌐 Scribe server at http://localhost:{PORT}/graph/index.html")
    print("   Graph + RAG chat ready · Press Ctrl+C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")


if __name__ == "__main__":
    main()
