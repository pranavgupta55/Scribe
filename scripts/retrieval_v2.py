"""Graph-augmented retrieval v2 for Scribe.

Public entry point: retrieve_v2(query, chroma_dir, graph_path) -> dict.

============================================================================
ALGORITHM OUTLINE
============================================================================

Given a natural-language question, this module returns a bundle of
graph-anchored facts and transcript chunks that a downstream chatbot can
feed into a generation prompt.  The pipeline is deliberately structured so
each stage is inspectable via the returned "meta" block.

Step 1 — Decompose query into atomic sub-queries
    * Try local qwen3:1.7b via Ollama (chat with think=True) using the
      _DECOMPOSE_SYSTEM prompt copied verbatim from server.py so behavior
      matches v1 exactly.
    * On failure / timeout / malformed output, fall back to Gemini 2.5
      Flash via google-genai.
    * On any further failure, fall back to [query] (single sub-query).
    * Cap at MAX_SUB_QUERIES (5).
    * Whole decompose stage is time-budgeted to DECOMPOSE_TIMEOUT_SEC
      (2 seconds) — implemented by a worker thread + join(timeout).

Step 2 — Load graph_v2.json once and build lookup indices
    * by_id      : node_id -> node dict
    * edges_from : source_id -> [link, ...]
    * edges_to   : target_id -> [link, ...]
    * Cached in module-level singletons keyed by absolute graph_path so
      repeated calls in the same process do not re-parse the 5477-node
      graph.

Step 3 — Initial retrieval per sub-query
    * Embed each sub-query via qwen3-embedding:8b using the exact same
      prefix that was applied at index-build time
      ("Instruct: Retrieve semantically similar text.\\nQuery: ").
    * For each sub-query, query facts_v2 (n=3) and chunks_v2 (n=3).
    * ChromaDB returns cosine distance; convert to similarity = 1 - d.

Step 4 — Merge across sub-queries (union by id)
    * Facts merged by node_id (metadata['node_id'] falls back to id).
    * Chunks merged by chunk_id.
    * Similarity = max across sub-queries; sub_query_indices = union.

Step 5 — Expand each fact via graph_v2
    For each retrieved claim node:
      contradicts  — kind='contradiction'  (undirected: check both dirs)
      agrees       — kind='agreement'      (undirected: check both dirs)
      builds_on    — kind='builds-on'      (undirected: check both dirs)
      hosts        — kind='hosts', where the OTHER endpoint is the parent
                     concept (hosts edges are directed concept -> claim,
                     so we look at edges_to[claim] and take the source).
      illustrates  — walk each parent concept, then follow edges_from
                     where kind='illustrates' (concept -> example).
                     Total budget MAX_ILLUSTRATES across all parents.
      practices    — same shape as illustrates but kind='practices'
                     (concept -> practice), budget MAX_PRACTICES.
      related      — DELIBERATELY SKIPPED.  The 'related' edge is noisy
                     (9709 of 19154 total edges) and would flood the
                     expansion set without adding much signal; when a
                     fact truly needs semantic neighbors, sub-query
                     retrieval already surfaces them.
    Empty lists are legitimate — we do NOT fall back to another kind
    or force multi-hop when a fact has zero neighbors of a given kind.

Step 6 — Chunk side-effect expansion
    * Embed each expanded neighbor's text (batched by qwen3.embed).
    * Query chunks_v2 with n_results=1 for each.
    * If similarity >= 0.5 and chunk_id not already retrieved, add it as
      a side-effect chunk with is_side_effect=True.
    * Cap at MAX_SIDE_EFFECT_CHUNKS (8) — drop lowest-similarity if over.

Step 7 — Dedup expansions
    Step 7a — union by id.  If the same neighbor node_id appears under
        multiple kinds for a single fact, keep the highest-weight kind
        and drop the duplicates.  Count drops in
        meta.n_dedup_dropped_by_id.
    Step 7b — text-cos-sim within-kind.  For each kind list, embed all
        neighbor texts (batched) and drop the lower-weight member of any
        pair whose cosine similarity >= SIM_DEDUP_THRESHOLD (0.95).
        Count drops in meta.n_dedup_dropped_by_sim.

Step 8 — Sort & return
    * Facts sorted by similarity descending.
    * Chunks sorted with non-side-effects first (similarity desc), then
      side-effects (similarity desc).

============================================================================
Constants (all defined at the top; edit there, not inline).
============================================================================
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_DEDUP_THRESHOLD = 0.95
EMBED_MODEL = "qwen3-embedding:8b"
QWEN3_PREFIX = "Instruct: Retrieve semantically similar text.\nQuery: "
MAX_SUB_QUERIES = 5
MAX_SIDE_EFFECT_CHUNKS = 8
DECOMPOSE_TIMEOUT_SEC = 2.0
SIDE_EFFECT_SIM_THRESHOLD = 0.5

# Per-fact expansion caps
MAX_CONTRADICTS = 5
MAX_AGREES = 2
MAX_BUILDS_ON = 3
MAX_HOSTS = 2
MAX_ILLUSTRATES = 2
MAX_PRACTICES = 10

# Chroma retrieval widths
N_FACTS_PER_SQ = 3
N_CHUNKS_PER_SQ = 3

# Kind -> weight (mirrors graph_v2 builder; used when comparing kinds for
# dedup where higher weight wins).
_KIND_WEIGHT = {
    "contradiction": 1.0,
    "agreement": 0.95,
    "builds-on": 0.8,
    "related": 0.6,
    "hosts": 0.5,
    "practices": 0.5,
    "illustrates": 0.5,
}

# Ollama / Gemini config borrowed from server.py.
QWEN_DECOMPOSE_MODEL = "qwen3:1.7b"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# Verbatim from server.py:_DECOMPOSE_SYSTEM.  Kept as a literal string so
# this file has zero import dependency on server.py.
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
<input>What impact did Alex's early failures have on his current business philosophy?</input>
<output>
What are Alex's early business failures?
What is Alex's current business philosophy?
</output>
</example>
</examples>"""


# ---------------------------------------------------------------------------
# Module-level singletons (graph index cache)
# ---------------------------------------------------------------------------
# Keyed by absolute graph_path so a caller that passes different paths in
# the same process still gets correct indices.  _graph_cache_lock guards
# initialization only; reads after init are lock-free (dict is immutable
# once built).
_graph_cache: dict[str, dict[str, Any]] = {}
_graph_cache_lock = threading.Lock()


def _load_graph(graph_path: str) -> dict[str, Any]:
    """Load graph_v2.json and build (by_id, edges_from, edges_to) indices.
    Cached per absolute path — subsequent calls in the same process are
    O(1) dict lookups."""
    abs_path = os.path.abspath(graph_path)
    cached = _graph_cache.get(abs_path)
    if cached is not None:
        return cached
    with _graph_cache_lock:
        cached = _graph_cache.get(abs_path)
        if cached is not None:
            return cached
        with open(abs_path, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
        by_id: dict[str, dict] = {n["id"]: n for n in graph.get("nodes", [])}
        edges_from: dict[str, list[dict]] = {}
        edges_to: dict[str, list[dict]] = {}
        for link in graph.get("links", []):
            edges_from.setdefault(link["source"], []).append(link)
            edges_to.setdefault(link["target"], []).append(link)
        cached = {
            "by_id": by_id,
            "edges_from": edges_from,
            "edges_to": edges_to,
        }
        _graph_cache[abs_path] = cached
        return cached


# ---------------------------------------------------------------------------
# Step 1 — decompose_query
# ---------------------------------------------------------------------------


def _parse_decompose_output(text: str) -> list[str] | None:
    """Parse decomposition LLM output.  Same rules as server.py:_parse:
    strip <think>, extract <output>, drop stray xml, strip bullets."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    m = re.search(r"<output>\s*(.*?)\s*</output>", text, re.DOTALL)
    raw = m.group(1) if m else text
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("<") and not line.startswith(">")]
    lines = [re.sub(r"^[\d]+[\.\)]\s*|^[-*]\s+", "", line).strip() for line in lines]
    lines = [line for line in lines if line]
    if 1 <= len(lines) <= MAX_SUB_QUERIES:
        return lines
    if len(lines) > MAX_SUB_QUERIES:
        # Cap rather than reject when too many.
        return lines[:MAX_SUB_QUERIES]
    return None


def _decompose_qwen(query: str) -> list[str] | None:
    """Call local Ollama qwen3:1.7b with the decomposition prompt."""
    import ollama

    resp = ollama.chat(
        model=QWEN_DECOMPOSE_MODEL,
        messages=[
            {"role": "system", "content": _DECOMPOSE_SYSTEM},
            {"role": "user", "content": f"<input>{query}</input>"},
        ],
        think=True,
        options={"num_predict": 800, "temperature": 0},
    )
    return _parse_decompose_output(resp["message"]["content"])


def _decompose_gemini(query: str) -> list[str] | None:
    """Fallback: Gemini 2.5 Flash via google-genai."""
    api_key = os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key:
        return None
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_DECOMPOSE_SYSTEM + f"\n\n<input>{query}</input>",
        config=gtypes.GenerateContentConfig(
            temperature=0,
            max_output_tokens=200,
            thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
        ),
    )
    return _parse_decompose_output(resp.text or "")


def _decompose_query(query: str) -> list[str]:
    """Time-budgeted decomposition.  Runs qwen3 then Gemini in a worker
    thread; if the whole thing exceeds DECOMPOSE_TIMEOUT_SEC we abandon
    the worker (Python threads can't be killed cleanly, but its output is
    ignored) and return [query].
    """
    result: list[list[str] | None] = [None]

    def _worker() -> None:
        try:
            r = _decompose_qwen(query)
            if r:
                result[0] = r
                return
        except Exception:
            pass
        try:
            r = _decompose_gemini(query)
            if r:
                result[0] = r
                return
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=DECOMPOSE_TIMEOUT_SEC)
    if result[0]:
        return result[0][:MAX_SUB_QUERIES]
    return [query]


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _embed(texts: list[str]) -> list[list[float]]:
    """Batch-embed via Ollama qwen3-embedding:8b.  Returns [] on empty
    input.  Raises on model error — caller decides whether to swallow."""
    if not texts:
        return []
    import ollama

    resp = ollama.embed(model=EMBED_MODEL, input=texts)
    # ollama-python returns {"embeddings": [[...], ...]} for input=list
    return list(resp.get("embeddings") or [])


def _embed_sub_queries(sub_queries: list[str]) -> list[list[float]]:
    """Prefix each sub-query with the qwen3 retrieval instruction, then
    embed.  Matches how the facts collection was indexed."""
    return _embed([QWEN3_PREFIX + sq for sq in sub_queries])


def _cos_sim(a: list[float], b: list[float]) -> float:
    """Plain-python cosine.  Guards zero-vectors."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Graph expansion helpers
# ---------------------------------------------------------------------------


def _node_text(node: dict[str, Any] | None) -> str:
    """label + description, matching what facts_v2 embeds for retrieval.
    Defensive against missing fields."""
    if not node:
        return ""
    label = (node.get("label") or "").strip()
    desc = (node.get("description") or "").strip()
    if label and desc:
        return f"{label}: {desc}"
    return label or desc


# Regex to extract the source filename attached to example/practice
# descriptions.  Nodes carry "_from FILENAME.txt_" in their description
# body (see the graph_v2 export format).  Not every node has one; return
# None gracefully.
_SOURCE_RX = re.compile(r"_from\s+([A-Za-z0-9_\-\.]+\.txt)_")


def _traceable_source(node: dict[str, Any] | None) -> str | None:
    """Best-effort source-file attribution for a neighbor node."""
    if not node:
        return None
    desc = node.get("description") or ""
    m = _SOURCE_RX.search(desc)
    if m:
        return m.group(1)
    # Claims embed attribution_json in the chroma metadata, not on the
    # graph node itself, so we cannot recover a single source cheaply
    # here; return None rather than fabricating one.
    return None


def _peer_neighbors(
    node_id: str,
    kind: str,
    edges_from: dict[str, list[dict]],
    edges_to: dict[str, list[dict]],
    limit: int,
) -> list[tuple[str, dict]]:
    """For an undirected peer edge (contradiction / agreement / builds-on),
    scan both directions and return (neighbor_id, link) tuples, deduped by
    neighbor_id, sorted by link weight descending, capped at `limit`.
    """
    if limit <= 0:
        return []
    seen: dict[str, dict] = {}
    for link in edges_from.get(node_id, ()):
        if link.get("kind") != kind:
            continue
        other = link["target"]
        if other == node_id:
            continue
        # keep the highest-weight duplicate if we see the same neighbor twice
        prev = seen.get(other)
        if prev is None or (link.get("weight", 0) > prev.get("weight", 0)):
            seen[other] = link
    for link in edges_to.get(node_id, ()):
        if link.get("kind") != kind:
            continue
        other = link["source"]
        if other == node_id:
            continue
        prev = seen.get(other)
        if prev is None or (link.get("weight", 0) > prev.get("weight", 0)):
            seen[other] = link
    ranked = sorted(seen.items(), key=lambda kv: -kv[1].get("weight", 0.0))
    return ranked[:limit]


def _parent_concepts(
    node_id: str,
    edges_to: dict[str, list[dict]],
    limit: int,
) -> list[tuple[str, dict]]:
    """Return up to `limit` parent concepts of a claim via 'hosts' edges.
    hosts is directed concept -> claim, so parents live in edges_to."""
    if limit <= 0:
        return []
    out: list[tuple[str, dict]] = []
    for link in edges_to.get(node_id, ()):
        if link.get("kind") == "hosts":
            out.append((link["source"], link))
    # No natural sort key for parents; preserve original order but cap.
    return out[:limit]


def _concept_children(
    concept_id: str,
    kind: str,
    edges_from: dict[str, list[dict]],
) -> list[tuple[str, dict]]:
    """All children of `concept_id` under a specific hierarchical kind
    ('illustrates' -> example nodes; 'practices' -> practice nodes)."""
    out: list[tuple[str, dict]] = []
    for link in edges_from.get(concept_id, ()):
        if link.get("kind") == kind:
            out.append((link["target"], link))
    return out


def _make_neighbor(node_id: str, link: dict, kind_label: str,
                   by_id: dict[str, dict]) -> dict[str, Any]:
    """Build the 'neighbor' record for the output schema."""
    node = by_id.get(node_id)
    return {
        "node_id": node_id,
        "text": _node_text(node),
        "kind": kind_label,
        "weight": link.get("weight"),
        "confidence": link.get("confidence"),
        "source": _traceable_source(node),
    }


def _expand_fact(
    node_id: str,
    by_id: dict[str, dict],
    edges_from: dict[str, list[dict]],
    edges_to: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Run Step 5 expansion for a single fact.  Returns a dict of six
    lists keyed by output-schema kind names ('contradicts', 'agrees',
    'builds_on', 'hosts', 'illustrates', 'practices')."""
    contradicts = [
        _make_neighbor(nid, link, "contradicts", by_id)
        for nid, link in _peer_neighbors(node_id, "contradiction",
                                         edges_from, edges_to,
                                         MAX_CONTRADICTS)
    ]
    agrees = [
        _make_neighbor(nid, link, "agrees", by_id)
        for nid, link in _peer_neighbors(node_id, "agreement",
                                         edges_from, edges_to, MAX_AGREES)
    ]
    builds_on = [
        _make_neighbor(nid, link, "builds_on", by_id)
        for nid, link in _peer_neighbors(node_id, "builds-on",
                                         edges_from, edges_to,
                                         MAX_BUILDS_ON)
    ]

    parents = _parent_concepts(node_id, edges_to, MAX_HOSTS)
    hosts = [
        _make_neighbor(pid, link, "hosts", by_id)
        for pid, link in parents
    ]

    # Walk each parent for its 'illustrates' / 'practices' children.
    # We collect from ALL parents, then cap the TOTAL — a fact whose
    # first parent has 10 examples fills the budget alone; a fact whose
    # first parent has 0 examples still gets a shot from parent #2.
    illus_pool: list[tuple[str, dict]] = []
    prac_pool: list[tuple[str, dict]] = []
    for pid, _plink in parents:
        illus_pool.extend(_concept_children(pid, "illustrates", edges_from))
        prac_pool.extend(_concept_children(pid, "practices", edges_from))

    # De-dup by target node id in case multiple parents point to the
    # same example/practice (unlikely given graph structure, but cheap).
    def _dedup_ordered(pairs: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        seen: set[str] = set()
        out: list[tuple[str, dict]] = []
        for nid, link in pairs:
            if nid in seen:
                continue
            seen.add(nid)
            out.append((nid, link))
        return out

    illus_pool = _dedup_ordered(illus_pool)[:MAX_ILLUSTRATES]
    prac_pool = _dedup_ordered(prac_pool)[:MAX_PRACTICES]

    illustrates = [
        _make_neighbor(nid, link, "illustrates", by_id)
        for nid, link in illus_pool
    ]
    practices = [
        _make_neighbor(nid, link, "practices", by_id)
        for nid, link in prac_pool
    ]

    return {
        "contradicts": contradicts,
        "agrees": agrees,
        "builds_on": builds_on,
        "hosts": hosts,
        "illustrates": illustrates,
        "practices": practices,
    }


# ---------------------------------------------------------------------------
# Step 7 — dedup
# ---------------------------------------------------------------------------


def _dedup_by_id(expansions: dict[str, list[dict]]) -> int:
    """Step 7a: if the same node_id appears under multiple kinds, keep
    only the occurrence with the highest _KIND_WEIGHT and drop the others.
    Mutates `expansions` in place.  Returns count of dropped entries."""
    # Build node_id -> [(kind, index), ...] map.
    seen: dict[str, list[tuple[str, int]]] = {}
    for kind, lst in expansions.items():
        for idx, item in enumerate(lst):
            seen.setdefault(item["node_id"], []).append((kind, idx))

    drops: dict[str, set[int]] = {k: set() for k in expansions}
    dropped = 0
    for _nid, occurrences in seen.items():
        if len(occurrences) <= 1:
            continue
        # Pick winner: highest kind weight; on tie, first occurrence wins.
        winner = max(
            occurrences,
            key=lambda ki: _KIND_WEIGHT.get(_kind_to_graph(ki[0]), 0.0),
        )
        for kind, idx in occurrences:
            if (kind, idx) == winner:
                continue
            drops[kind].add(idx)
            dropped += 1

    for kind, drop_set in drops.items():
        if drop_set:
            expansions[kind] = [
                item for i, item in enumerate(expansions[kind])
                if i not in drop_set
            ]
    return dropped


def _kind_to_graph(schema_kind: str) -> str:
    """Map schema kind name -> the graph edge kind used in _KIND_WEIGHT."""
    return {
        "contradicts": "contradiction",
        "agrees": "agreement",
        "builds_on": "builds-on",
        "hosts": "hosts",
        "illustrates": "illustrates",
        "practices": "practices",
    }.get(schema_kind, schema_kind)


def _dedup_within_kind_by_similarity(expansions: dict[str, list[dict]]) -> int:
    """Step 7b: for each kind, if two neighbor texts have cos-sim >=
    SIM_DEDUP_THRESHOLD, drop the lower-weight one (ties: keep earlier).
    Uses qwen3 embeddings batched per kind.  Returns count of dropped."""
    dropped = 0
    for kind, lst in list(expansions.items()):
        if len(lst) < 2:
            continue
        texts = [item.get("text") or "" for item in lst]
        try:
            embs = _embed(texts)
        except Exception:
            # If embedding fails we skip similarity-dedup for this kind
            # rather than crashing the whole retrieval.
            continue
        drop_idx: set[int] = set()
        n = len(lst)
        for i in range(n):
            if i in drop_idx:
                continue
            for j in range(i + 1, n):
                if j in drop_idx:
                    continue
                if _cos_sim(embs[i], embs[j]) >= SIM_DEDUP_THRESHOLD:
                    # keep the higher-weight one
                    wi = lst[i].get("weight") or 0
                    wj = lst[j].get("weight") or 0
                    if wj > wi:
                        drop_idx.add(i)
                        break  # i is dropped; move on to next i
                    else:
                        drop_idx.add(j)
        if drop_idx:
            expansions[kind] = [item for k, item in enumerate(lst) if k not in drop_idx]
            dropped += len(drop_idx)
    return dropped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def retrieve_v2(
    query: str,
    chroma_dir: str = ".chroma/",
    graph_path: str = "graph/graph_v2.json",
) -> dict[str, Any]:
    """Graph-augmented retrieval.  See module docstring for algorithm."""
    import chromadb

    meta: dict[str, Any] = {
        "decompose_ms": 0,
        "retrieve_ms": 0,
        "expand_ms": 0,
        "dedup_ms": 0,
        "n_dedup_dropped_by_id": 0,
        "n_dedup_dropped_by_sim": 0,
        "n_side_effect_chunks": 0,
    }

    # --- Step 1: decompose ---------------------------------------------
    t0 = time.perf_counter()
    sub_queries = _decompose_query(query)
    meta["decompose_ms"] = int((time.perf_counter() - t0) * 1000)

    # --- Step 2: load graph indices (cached) ---------------------------
    graph = _load_graph(graph_path)
    by_id = graph["by_id"]
    edges_from = graph["edges_from"]
    edges_to = graph["edges_to"]

    # --- Step 3: initial retrieval per sub-query -----------------------
    t0 = time.perf_counter()
    client = chromadb.PersistentClient(path=chroma_dir)
    facts_col = client.get_collection("facts_v2")
    chunks_col = client.get_collection("chunks_v2")

    sq_embeddings = _embed_sub_queries(sub_queries)

    # facts_state[node_id] = {"similarity": max_sim, "sub_query_indices":
    # set(int), "metadata": dict, "document": str}
    facts_state: dict[str, dict[str, Any]] = {}
    chunks_state: dict[str, dict[str, Any]] = {}

    for sq_idx, emb in enumerate(sq_embeddings):
        # Facts
        try:
            fr = facts_col.query(
                query_embeddings=[emb],
                n_results=N_FACTS_PER_SQ,
                include=["metadatas", "documents", "distances"],
            )
        except Exception:
            fr = {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        ids = (fr.get("ids") or [[]])[0]
        dists = (fr.get("distances") or [[]])[0]
        docs = (fr.get("documents") or [[]])[0]
        metas = (fr.get("metadatas") or [[]])[0]
        for _id, dist, doc, m in zip(ids, dists, docs, metas):
            node_id = (m or {}).get("node_id") or _id
            sim = 1.0 - float(dist)
            st = facts_state.get(node_id)
            if st is None:
                facts_state[node_id] = {
                    "similarity": sim,
                    "sub_query_indices": {sq_idx},
                    "metadata": m or {},
                    "document": doc,
                    "node_id": node_id,
                }
            else:
                st["similarity"] = max(st["similarity"], sim)
                st["sub_query_indices"].add(sq_idx)

        # Chunks
        try:
            cr = chunks_col.query(
                query_embeddings=[emb],
                n_results=N_CHUNKS_PER_SQ,
                include=["metadatas", "documents", "distances"],
            )
        except Exception:
            cr = {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        ids = (cr.get("ids") or [[]])[0]
        dists = (cr.get("distances") or [[]])[0]
        docs = (cr.get("documents") or [[]])[0]
        metas = (cr.get("metadatas") or [[]])[0]
        for _id, dist, doc, m in zip(ids, dists, docs, metas):
            chunk_id = _id
            sim = 1.0 - float(dist)
            st = chunks_state.get(chunk_id)
            if st is None:
                chunks_state[chunk_id] = {
                    "similarity": sim,
                    "sub_query_indices": {sq_idx},
                    "metadata": m or {},
                    "document": doc,
                    "chunk_id": chunk_id,
                }
            else:
                st["similarity"] = max(st["similarity"], sim)
                st["sub_query_indices"].add(sq_idx)

    meta["retrieve_ms"] = int((time.perf_counter() - t0) * 1000)

    # --- Step 5: expand each fact --------------------------------------
    t0 = time.perf_counter()
    fact_records: list[dict[str, Any]] = []
    for node_id, st in facts_state.items():
        expansions = _expand_fact(node_id, by_id, edges_from, edges_to)
        # Metadata straight from chroma.
        m = st["metadata"] or {}
        fact_records.append({
            "node_id": node_id,
            "text": st["document"] or "",
            "topic": m.get("topic"),
            "level": m.get("level"),
            "source_count": m.get("source_count"),
            "similarity": st["similarity"],
            "sub_query_indices": sorted(st["sub_query_indices"]),
            "expansions": expansions,
        })
    meta["expand_ms"] = int((time.perf_counter() - t0) * 1000)

    # --- Step 7: dedup expansions --------------------------------------
    t0 = time.perf_counter()
    total_id_drops = 0
    total_sim_drops = 0
    for fact in fact_records:
        total_id_drops += _dedup_by_id(fact["expansions"])
        total_sim_drops += _dedup_within_kind_by_similarity(fact["expansions"])
    meta["n_dedup_dropped_by_id"] = total_id_drops
    meta["n_dedup_dropped_by_sim"] = total_sim_drops
    meta["dedup_ms"] = int((time.perf_counter() - t0) * 1000)

    # --- Step 6: chunk side-effect expansion ---------------------------
    # (Runs after dedup so we don't waste embeddings on neighbors that
    # were about to be dropped.)
    side_effect_candidates: list[tuple[float, dict[str, Any]]] = []
    neighbor_texts: list[str] = []
    for fact in fact_records:
        for kind_list in fact["expansions"].values():
            for n in kind_list:
                if n.get("text"):
                    neighbor_texts.append(n["text"])

    if neighbor_texts:
        try:
            neigh_embs = _embed(neighbor_texts)
        except Exception:
            neigh_embs = []
        # For each neighbor embedding, query chunks_v2 n=1.
        for emb in neigh_embs:
            try:
                cr = chunks_col.query(
                    query_embeddings=[emb],
                    n_results=1,
                    include=["metadatas", "documents", "distances"],
                )
            except Exception:
                continue
            ids = (cr.get("ids") or [[]])[0]
            dists = (cr.get("distances") or [[]])[0]
            docs = (cr.get("documents") or [[]])[0]
            metas = (cr.get("metadatas") or [[]])[0]
            if not ids:
                continue
            chunk_id = ids[0]
            sim = 1.0 - float(dists[0])
            if sim < SIDE_EFFECT_SIM_THRESHOLD:
                continue
            if chunk_id in chunks_state:
                continue
            candidate = {
                "similarity": sim,
                "sub_query_indices": set(),  # side-effect: not tied to sub-query
                "metadata": metas[0] or {},
                "document": docs[0],
                "chunk_id": chunk_id,
                "is_side_effect": True,
            }
            side_effect_candidates.append((sim, candidate))

    # Cap side-effects at MAX_SIDE_EFFECT_CHUNKS, keeping highest similarity.
    side_effect_candidates.sort(key=lambda t: -t[0])
    kept_side_effects: dict[str, dict[str, Any]] = {}
    for sim, cand in side_effect_candidates:
        if len(kept_side_effects) >= MAX_SIDE_EFFECT_CHUNKS:
            break
        cid = cand["chunk_id"]
        if cid in kept_side_effects:
            # duplicate side-effect for the same chunk_id — keep higher sim
            if sim > kept_side_effects[cid]["similarity"]:
                kept_side_effects[cid] = cand
            continue
        kept_side_effects[cid] = cand
    meta["n_side_effect_chunks"] = len(kept_side_effects)

    # --- Assemble final chunks list -----------------------------------
    def _chunk_record(st: dict[str, Any], is_side_effect: bool) -> dict[str, Any]:
        m = st["metadata"] or {}
        return {
            "chunk_id": st["chunk_id"],
            "source": m.get("source"),
            "section_idx": m.get("section_idx"),
            "section_title": m.get("section_title"),
            "premise": m.get("premise"),
            "conclusion": m.get("conclusion"),
            "text": st["document"] or "",
            "similarity": st["similarity"],
            "sub_query_indices": sorted(st.get("sub_query_indices", set())),
            "is_side_effect": is_side_effect,
        }

    primary_chunks = [_chunk_record(st, False) for st in chunks_state.values()]
    side_effect_chunks = [_chunk_record(st, True) for st in kept_side_effects.values()]

    # --- Step 8: sort ---------------------------------------------------
    fact_records.sort(key=lambda f: -(f["similarity"] or 0.0))
    primary_chunks.sort(key=lambda c: -(c["similarity"] or 0.0))
    side_effect_chunks.sort(key=lambda c: -(c["similarity"] or 0.0))

    return {
        "sub_queries": sub_queries,
        "facts": fact_records,
        "chunks": primary_chunks + side_effect_chunks,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("usage: python3 scripts/retrieval_v2.py \"your question\"", file=sys.stderr)
        sys.exit(2)
    query = " ".join(sys.argv[1:])
    # Resolve default paths relative to the repo root (parent of scripts/).
    repo_root = Path(__file__).resolve().parent.parent
    chroma_dir = str(repo_root / ".chroma")
    graph_path = str(repo_root / "graph" / "graph_v2.json")
    result = retrieve_v2(query, chroma_dir=chroma_dir, graph_path=graph_path)
    print(json.dumps(result, indent=2, default=str))
    facts = result.get("facts", [])
    chunks = result.get("chunks", [])
    n_side = sum(1 for c in chunks if c.get("is_side_effect"))
    print(
        f"\nFacts: {len(facts)}, "
        f"Chunks: {len(chunks)} ({n_side} side-effect), "
        f"Sub-queries: {result.get('sub_queries')}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    _main()
