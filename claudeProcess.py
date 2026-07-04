#!/usr/bin/env python3
"""
Scribe knowledge pipeline — Anthropic Claude backend (Opus manager + Haiku workers).

Drop-in replacement for `process.py`'s LLM layer. Preserves the exact 4-pass structure,
prompts, and knowledge/*.json + knowledge/topics/*.md output schema so downstream code
(retrieval, Atlas, server.py) keeps working. Only the LLM backend swaps:

  * Pass A whole-transcript SEGMENT       → Opus  (manager reads full transcript)
  * Pass A per-window plan/topics/sections → Haiku (parallel across windows)
  * Pass A REDUCE                          → Haiku
  * Pass B B0 plan, B1 draft, B2 verify    → Haiku (parallel across sections)
  * Pass C C0 plan, C1 structure           → Haiku (parallel across topics)
  * Pass D D0 plan, D1 write               → Haiku (parallel across suites)
  * Any Haiku unit that fails after 2 retries → Opus takes over as fallback rescue.

Concurrency: a ThreadPoolExecutor (default 8 workers) dispatches Haiku calls in parallel.
Opus stays serial (1 per transcript, cheap because it's used sparingly).

Retry/fallback: on Haiku failure (retry 2x with exp backoff), Opus takes over that unit.
Rate-limit 429s use exp backoff via the Anthropic SDK's built-in retry. Ollama fallback is
REMOVED — the whole point is to eliminate qwen slowness.

Embeddings stay LOCAL (nomic-embed-text via Ollama). The stored vector space + retrieval
depend on nomic — do not touch.

Usage:
    python3 process_claude.py <transcript.txt>              # process a single file
    python3 process_claude.py --all                         # process all unprocessed
    python3 process_claude.py --rebuild                     # wipe ChromaDB + reprocess
    python3 process_claude.py --connect                     # (re)run Pass D only
    python3 process_claude.py --knowledge-dir DIR ...       # write outputs to DIR instead

The last flag lets you dual-run against a fresh scratch dir without disturbing the real
knowledge/ folder currently being written by an ongoing Gemini process.
"""

from __future__ import annotations

import os
import sys
import json
import time
import re
import shutil
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# We reuse EVERYTHING from process.py that is not the LLM backend:
#   prompts, windowing, snap/dedupe helpers, chunk splitting, embed(), collections,
#   topic-file writer, index rebuilder, Pass D orchestration wrappers, etc.
# We only override the LLM call layer.
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

import process as P  # noqa: E402 — the whole existing pipeline

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPUS_MODEL = os.environ.get("SCRIBE_OPUS_MODEL", "claude-opus-4-7")
HAIKU_MODEL = os.environ.get("SCRIBE_HAIKU_MODEL", "claude-haiku-4-5")
HAIKU_WORKERS = int(os.environ.get("SCRIBE_HAIKU_WORKERS", "8"))

# Retry budget for Haiku before escalating to Opus fallback for that unit.
HAIKU_RETRIES = 2
HAIKU_BACKOFF_BASE = 1.5  # seconds — exponential: 1.5, 2.25

# Per-call timeout hard-cap (seconds). The SDK also has its own; this is defense.
CALL_TIMEOUT_S = 120

# Anthropic pricing (USD per 1M tokens, input / output). Used for cost accounting only.
PRICE = {
    "opus": (15.0, 75.0),
    "haiku": (0.80, 4.0),
}

# ---------------------------------------------------------------------------
# Anthropic client bootstrap — reads ANTHROPIC_API_KEY from env or Atlas .env
# ---------------------------------------------------------------------------

def _load_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # Fallback: source from Atlas .env if present.
    atlas_env = Path.home() / "VSCode Projects" / "Atlas" / ".env"
    # Also try the absolute path the user specified.
    for candidate in (
        Path("/Users/pranavgupta/VSCode Projects/Atlas/.env"),
        atlas_env,
    ):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        os.environ["ANTHROPIC_API_KEY"] = val
                        return val
    raise RuntimeError(
        "ANTHROPIC_API_KEY not found. Set it in env or in Atlas/.env."
    )


_anthropic_client = None
_client_lock = threading.Lock()


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        with _client_lock:
            if _anthropic_client is None:
                import anthropic
                _load_anthropic_key()
                # SDK handles 429 retry-after with exp backoff by default (max_retries=2).
                # Bump to 4 for extra resilience during long transcripts.
                _anthropic_client = anthropic.Anthropic(max_retries=4, timeout=CALL_TIMEOUT_S)
    return _anthropic_client


# ---------------------------------------------------------------------------
# Call accounting (thread-safe) — for cost estimation + summary
# ---------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats = {
    "opus":  {"calls": 0, "in_tok": 0, "out_tok": 0, "fallback_saves": 0, "errors": 0},
    "haiku": {"calls": 0, "in_tok": 0, "out_tok": 0, "retries": 0, "errors": 0},
}


def _account(model_bucket: str, usage, *, retry: bool = False, error: bool = False,
             fallback_save: bool = False):
    with _stats_lock:
        s = _stats[model_bucket]
        s["calls"] += 1
        if usage:
            s["in_tok"] += getattr(usage, "input_tokens", 0) or 0
            s["out_tok"] += getattr(usage, "output_tokens", 0) or 0
        if retry:
            s["retries"] = s.get("retries", 0) + 1
        if error:
            s["errors"] += 1
        if fallback_save:
            s["fallback_saves"] += 1


def _cost_summary() -> str:
    """Return a compact one-line cost summary using PRICE constants."""
    opus_in, opus_out = PRICE["opus"]
    haiku_in, haiku_out = PRICE["haiku"]
    o = _stats["opus"]
    h = _stats["haiku"]
    opus_cost = (o["in_tok"] * opus_in + o["out_tok"] * opus_out) / 1_000_000
    haiku_cost = (h["in_tok"] * haiku_in + h["out_tok"] * haiku_out) / 1_000_000
    return (
        f"Opus: {o['calls']} calls, {o['in_tok']:,} in / {o['out_tok']:,} out tok, "
        f"${opus_cost:.4f}  ·  "
        f"Haiku: {h['calls']} calls, {h['in_tok']:,} in / {h['out_tok']:,} out tok, "
        f"${haiku_cost:.4f}  ·  "
        f"total ${opus_cost + haiku_cost:.4f}  "
        f"(fallback saves: {o['fallback_saves']}, haiku retries: {h.get('retries', 0)})"
    )


# ---------------------------------------------------------------------------
# Core LLM call — JSON mode via Claude
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extractor: strip fences, find the first {...} block."""
    if not text:
        return None
    txt = text.strip()
    # Strip common fences the model sometimes adds despite instructions.
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        # Fallback: locate outermost JSON object.
        start = txt.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(txt)):
            c = txt[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(txt[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None


def _claude_json(model: str, system: str, user: str, *, max_tokens: int,
                 temperature: float) -> dict | None:
    """One Claude message call in JSON mode, parsed. None on unrecoverable failure.

    Opus 4.7 (and other extended-thinking models) reject non-default `temperature`;
    we omit the parameter entirely for opus and pass it only for haiku."""
    client = _client()
    bucket = "opus" if "opus" in model else "haiku"
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{
            "role": "user",
            "content": user + "\n\nRespond with a single JSON object and nothing else.",
        }],
    )
    if bucket == "haiku":
        kwargs["temperature"] = temperature
    try:
        resp = client.messages.create(**kwargs)
        parts = getattr(resp, "content", []) or []
        text = ""
        for blk in parts:
            t = getattr(blk, "text", None)
            if t:
                text += t
        parsed = _extract_json(text)
        _account(bucket, getattr(resp, "usage", None))
        return parsed
    except Exception as e:  # noqa: BLE001
        _account(bucket, None, error=True)
        # Re-raise so the retry/fallback layer can catch it.
        raise


def _llm_json_claude(system: str, user: str, *, max_tokens: int, temperature: float,
                     prefer: str = "haiku") -> dict | None:
    """Dispatch a JSON call. `prefer` selects the primary model:
       - "haiku": Haiku with retries; on final failure, Opus takes over the unit.
       - "opus":  Opus directly (used for the manager verification step, whole-segment).
    """
    if prefer == "opus":
        # Opus is the manager — do not fall back to a weaker model. Let it raise if the
        # SDK's built-in retries + max_retries=4 are exhausted.
        try:
            return _claude_json(OPUS_MODEL, system, user,
                                max_tokens=max_tokens, temperature=temperature)
        except Exception as e:  # noqa: BLE001
            print(f"    ⚠️  Opus call failed after SDK retries: {str(e)[:120]}")
            return None

    # Haiku primary: try HAIKU_RETRIES+1 times with exp backoff, then Opus fallback.
    last_err: Exception | None = None
    for attempt in range(HAIKU_RETRIES + 1):
        try:
            out = _claude_json(HAIKU_MODEL, system, user,
                               max_tokens=max_tokens, temperature=temperature)
            if out is not None:
                return out
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < HAIKU_RETRIES:
            _account("haiku", None, retry=True)
            time.sleep(HAIKU_BACKOFF_BASE ** (attempt + 1))

    # Escalate to Opus for this unit.
    try:
        out = _claude_json(OPUS_MODEL, system, user,
                           max_tokens=max_tokens, temperature=temperature)
        if out is not None:
            _account("opus", None, fallback_save=True)
            return out
    except Exception as e:  # noqa: BLE001
        last_err = e
    if last_err:
        print(f"    ⚠️  Haiku→Opus escalation both failed: {str(last_err)[:120]}")
    return None


# ---------------------------------------------------------------------------
# Monkey-patch: replace process.py's LLM entrypoints so all the existing pipeline
# functions (llm_extract, llm_structure, analyze_connections, _segment_window,
# _reduce_topics, _segment_whole_gemini) automatically flow through Claude without
# touching their internal logic.
# ---------------------------------------------------------------------------

# Map process.py's system-prompt identifiers to routing preference.
# Opus role (manager): reads the whole transcript once to produce the topic/section
# plan (Pass A whole-segment). Everything else is Haiku, with Opus as fallback rescue
# when Haiku fails a unit after retries. This matches the spec's cost target
# (<$1/transcript avg): 1 Opus call/transcript + N cheap Haiku workers.
#
# NOTE: an earlier iteration also put Opus on the B2 verify step. On a 40k-word
# transcript with ~50 sections, that added ~$4.50 in Opus cost per transcript
# (200k input tokens × $15/M). Haiku's verify quality is sufficient; Opus stays
# reserved for whole-transcript planning + fallback.
_OPUS_SYSTEMS = {
    id(P._WHOLE_SEGMENT_SYSTEM),   # Pass A — whole-transcript segmentation (only Opus can hold it all coherently)
}


def _llm_replacement(system: str, user: str, opts: dict) -> dict | None:
    """Drop-in replacement for `process._llm`. Routes to Opus for the manager steps,
    Haiku for everything else. `opts` is the original Ollama options dict; we read
    `num_predict` and `temperature` from it so all call sites work unchanged."""
    max_tokens = max(int(opts.get("num_predict", 1024)), 1024)
    temperature = float(opts.get("temperature", 0.2))
    prefer = "opus" if id(system) in _OPUS_SYSTEMS else "haiku"
    return _llm_json_claude(system, user,
                            max_tokens=max_tokens, temperature=temperature, prefer=prefer)


def _llm_json_replacement(system: str, user: str, *, max_tokens: int,
                          temperature: float = 0.2, qwen_options=None) -> dict | None:
    """Drop-in replacement for `process.llm_json`. Same routing logic as `_llm_replacement`."""
    prefer = "opus" if id(system) in _OPUS_SYSTEMS else "haiku"
    return _llm_json_claude(system, user,
                            max_tokens=max_tokens, temperature=temperature, prefer=prefer)


# Threshold (in words): transcripts up to this size go through Opus whole-segment (best
# quality, single Opus call reads the whole thing). Longer transcripts use Haiku-windowed
# segmentation to keep cost < $1 / transcript — Opus at $15/M-in tok on a 40k-word
# transcript alone is ~$0.78 just for the segment step. Windowed Haiku is a small quality
# hit but stays well inside budget.
WHOLE_SEGMENT_MAX_WORDS = int(os.environ.get("SCRIBE_WHOLE_SEG_MAX_WORDS", "10000"))


def _size_aware_gemini_available():
    """Patched replacement for `process._gemini_available` that gates the whole-transcript
    path on transcript size. We can't see the transcript from inside this call, so we set
    a thread-local flag from `process_transcript` before calling `P.llm_segment`.
    """
    return True if _thread_local.get("use_whole_segment", True) else None


_thread_local: dict[str, Any] = {"use_whole_segment": True}


def install_claude_backend():
    """Monkey-patch process.py to route every LLM call through Claude.

    Idempotent — safe to call twice. Also disables Gemini's `_gemini_available` so it
    never even probes the SDK.
    """
    P._llm = _llm_replacement
    P.llm_json = _llm_json_replacement
    # Prevent Gemini probe: force it to think Gemini is available (so whole-transcript path
    # is preferred), but only when we've decided the transcript is small enough. See
    # `_size_aware_gemini_available` and the size check in `process_transcript`.
    P._gemini_available = _size_aware_gemini_available
    P._gemini_cooldown_until = 0.0
    # The existing `_llm_summary` reads process._llm_stats; leave it in place but zero it.
    P._llm_stats = {"gemini": 0, "qwen": 0, "qwen_fallback": 0}
    # Skip Ollama LLM check (embed still needs it).
    P.check_ollama = _check_ollama_embed_only


def _check_ollama_embed_only():
    """Verify only the embedding model is present in Ollama — LLM models no longer needed."""
    try:
        import ollama
        result = ollama.list()
        names = [m["model"] for m in result.get("models", [])]
        if not any(P.EMBED_MODEL.split(":")[0] in n for n in names):
            print(f"❌ Missing Ollama embed model: {P.EMBED_MODEL}")
            print(f"   Run: ollama pull {P.EMBED_MODEL}")
            sys.exit(1)
    except Exception:
        print("❌ Ollama is not running (needed for embeddings). Start it: ollama serve")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Parallelization — override the per-section / per-topic / per-suite loops so
# they dispatch Haiku work concurrently via ThreadPoolExecutor.
# ---------------------------------------------------------------------------

_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(max_workers=HAIKU_WORKERS,
                                               thread_name_prefix="scribe-haiku")
    return _EXECUTOR


def _parallel_pass_b(sections: list[dict], topics: list[str]) -> list[dict]:
    """Run Pass B (llm_extract) across all sections in parallel Haiku workers.

    Returns per-section extraction dicts in the same order as `sections`.
    """
    if not sections:
        return []
    ex = _get_executor()
    futs = {}
    for i, sec in enumerate(sections):
        title = (sec.get("title") or f"Section {i + 1}").strip()
        body = sec["text"]
        futs[ex.submit(P.llm_extract, body, title, topics)] = i

    results: list[dict | None] = [None] * len(sections)
    done_n = 0
    for fut in as_completed(futs):
        i = futs[fut]
        done_n += 1
        try:
            results[i] = fut.result()
        except Exception as e:  # noqa: BLE001
            print(f"    ⚠️  Pass B section {i} failed: {str(e)[:120]}")
            results[i] = {"claims": [], "entities": [], "triples": []}
        print(f"  [{done_n:>2}/{len(sections)}] extracted (parallel)" + " " * 30, end="\r")
    return [r or {"claims": [], "entities": [], "triples": []} for r in results]


def _parallel_pass_c(topics: list[str], claims_by_topic: dict[str, list[str]]) -> dict[str, dict]:
    """Run Pass C (llm_structure) across all topics in parallel. Returns {topic: structure}."""
    if not topics:
        return {}
    ex = _get_executor()
    futs = {}
    for t in topics:
        cl = claims_by_topic.get(t, [])
        if not cl:
            continue
        futs[ex.submit(P.llm_structure, t, cl)] = t

    out: dict[str, dict] = {}
    for fut in as_completed(futs):
        t = futs[fut]
        try:
            out[t] = fut.result() or {}
        except Exception as e:  # noqa: BLE001
            print(f"    ⚠️  Pass C topic '{t}' failed: {str(e)[:120]}")
            out[t] = {}
    return out


# ---------------------------------------------------------------------------
# Override `process_transcript` to use the parallel Pass B loop while preserving
# the exact output schema.
# ---------------------------------------------------------------------------

def process_transcript(transcript_path, force=False, knowledge_dir: Path | None = None):
    """Parallelized version of `process.process_transcript`. Same output schema.

    If `knowledge_dir` is provided, it overrides the P.KNOWLEDGE_DIR / TOPICS_DIR /
    SOURCES_FILE / CONNECTIONS_FILE / INDEX_FILE / CHROMA_DIR globals for THIS run
    so we can dual-run into a scratch dir without touching the real knowledge/.
    """
    from datetime import datetime

    # Optionally redirect all output paths to a scratch knowledge dir.
    if knowledge_dir is not None:
        knowledge_dir = Path(knowledge_dir).resolve()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "topics").mkdir(exist_ok=True)
        # Backup + reassign the module-level path globals.
        _saved = {
            "KNOWLEDGE_DIR":    P.KNOWLEDGE_DIR,
            "TOPICS_DIR":       P.TOPICS_DIR,
            "SOURCES_FILE":     P.SOURCES_FILE,
            "CONNECTIONS_FILE": P.CONNECTIONS_FILE,
            "INDEX_FILE":       P.INDEX_FILE,
            "CHROMA_DIR":       P.CHROMA_DIR,
        }
        P.KNOWLEDGE_DIR    = knowledge_dir
        P.TOPICS_DIR       = knowledge_dir / "topics"
        P.SOURCES_FILE     = knowledge_dir / "sources.json"
        P.CONNECTIONS_FILE = knowledge_dir / "connections.json"
        P.INDEX_FILE       = knowledge_dir / "_index.md"
        P.CHROMA_DIR       = knowledge_dir / ".chroma"
    else:
        _saved = None

    try:
        path = Path(transcript_path).resolve()
        name = path.name

        P.KNOWLEDGE_DIR.mkdir(exist_ok=True)
        P.TOPICS_DIR.mkdir(exist_ok=True)

        sources = P.load_json(P.SOURCES_FILE, {})
        if name in sources and not force:
            print(f"  ℹ️  {name} already processed — skipping.")
            return

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            print(f"  ⚠️  {name} is empty — skipping.")
            return

        word_count = len(text.split())
        print(f"\n📄 {name} — {word_count} words  [Claude backend]")
        t_start = time.time()

        # ── Pass A: SEGMENT ──
        # Small transcripts → Opus whole-transcript (best coherence, one manager call).
        # Long transcripts   → Haiku windowed map-reduce (keeps cost < $1/transcript).
        _thread_local["use_whole_segment"] = (word_count <= WHOLE_SEGMENT_MAX_WORDS)
        if _thread_local["use_whole_segment"]:
            print(f"  📖 Pass A — Opus whole-transcript ({word_count} words)")
        else:
            print(f"  📖 Pass A — Haiku windowed map-reduce ({word_count} words > "
                  f"{WHOLE_SEGMENT_MAX_WORDS} whole-segment threshold)")
        outline = P.llm_segment(text)
        topics_list = outline["topics"]
        sections = P.split_into_sections(text, outline["sections"])
        if not (P.MIN_TOPICS <= len(topics_list) <= P.MAX_TOPICS_GLOBAL):
            print(f"  ⚠️  topic count {len(topics_list)} outside target — proceeding")
        print(f"  🧭 Pass A — {len(sections)} sections · {len(topics_list)} canonical topics: "
              f"{', '.join(topics_list[:12])}{' …' if len(topics_list) > 12 else ''}"
              + " " * 8)

        facts_col, chunks_col = P.get_collections()

        # ── Pass B: EXTRACT (parallel Haiku across sections) ──
        print(f"  🚀 Pass B — dispatching {len(sections)} sections to Haiku pool "
              f"({HAIKU_WORKERS} workers)...")
        extractions = _parallel_pass_b(sections, topics_list)

        # Now serially write the sections + claims into ChromaDB (embed is Ollama-local,
        # ChromaDB writes are not concurrency-safe with the way we open it above).
        used_topics: set[str] = set()
        claim_count = 0
        entity_count = 0
        for i, (sec, extraction) in enumerate(zip(sections, extractions)):
            title = (sec.get("title") or f"Section {i + 1}").strip()
            body = sec["text"]
            chunks_col.upsert(
                ids=[f"{name}__s{i}"],
                documents=[body],
                embeddings=[P.embed(body)],
                metadatas=[{
                    "source":        name,
                    "section_idx":   i,
                    "section_title": title,
                    "premise":       (sec.get("premise") or "")[:500],
                    "conclusion":    (sec.get("conclusion") or "")[:500],
                }],
            )
            triples_json = json.dumps(extraction.get("triples", []))
            entity_count += len(extraction.get("entities", []))
            for c_idx, claim in enumerate(extraction.get("claims", [])):
                ctext  = claim["claim"]
                ctopic = claim["topic"]
                used_topics.add(ctopic)
                c_emb = P.embed(ctext)
                facts_col.upsert(
                    ids=[f"{name}__s{i}__c{c_idx}"],
                    documents=[ctext],
                    embeddings=[c_emb],
                    metadatas=[{
                        "source":        name,
                        "section_idx":   i,
                        "section_title": title,
                        "topic":         ctopic,
                        "triples":       triples_json,
                    }],
                )
                claim_count += 1

        print(f"  ✓ {claim_count} claims · {entity_count} entities · "
              f"{len(used_topics)} topics" + " " * 30)

        # Assemble topic notes (Pass C runs inside update_topic_file → llm_structure).
        # We parallelize update_topic_file across topics via a thread pool because
        # llm_structure is the heavy part; ChromaDB reads are thread-safe for .get().
        final_topics = sorted(used_topics)
        print(f"  → Assembling {len(final_topics)} topic note(s) (parallel Haiku)...")
        ex = _get_executor()
        futs = {ex.submit(P.update_topic_file, t, facts_col): t for t in final_topics}
        written = []
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                if fut.result():
                    written.append(t)
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠️  topic '{t}' assembly failed: {str(e)[:120]}")

        process_seconds = round(time.time() - t_start, 1)
        meta = P._read_meta(path)
        sources[name] = {
            "processed_at":      datetime.now().isoformat(),
            "video_summary":     outline["video_summary"],
            "word_count":        word_count,
            "window_count":      outline.get("n_windows", 1),
            "section_count":     len(sections),
            "claim_count":       claim_count,
            "entity_count":      entity_count,
            "url":               meta.get("url", ""),
            "title":             (meta.get("title") or "").strip()
                                    or P._prettify_source_name(name),
            "duration_seconds":  meta.get("duration_seconds"),
            "transcribe_seconds":meta.get("transcribe_seconds"),
            "process_seconds":   process_seconds,
            "topics":            sorted(written),
            "sections":          [
                {"title": (s.get("title") or f"Section {i + 1}").strip(),
                 "premise": (s.get("premise") or "").strip(),
                 "conclusion": (s.get("conclusion") or "").strip()}
                for i, s in enumerate(sections)
            ],
            "backend":           "claude",
        }
        P.save_json(P.SOURCES_FILE, sources)
        P.rebuild_index()

        print(f"  ✅ {name} complete — {process_seconds}s")
        print(f"  💰 {_cost_summary()}")

    finally:
        # Restore module-level path globals if we redirected them.
        if _saved is not None:
            for k, v in _saved.items():
                setattr(P, k, v)


def process_all(force=False, knowledge_dir: Path | None = None):
    if not P.TRANSCRIPTS_DIR.exists():
        print("❌ transcripts/ directory not found.")
        sys.exit(1)

    # Sources for skip-check are keyed by whichever knowledge dir we're writing to.
    src_file = (knowledge_dir / "sources.json") if knowledge_dir else P.SOURCES_FILE
    sources = P.load_json(src_file, {})
    all_txts = sorted(P.TRANSCRIPTS_DIR.glob("*.txt"))
    pending = all_txts if force else [t for t in all_txts if t.name not in sources]

    if not pending:
        print("✅ All transcripts already processed.")
        return

    print(f"Found {len(pending)} unprocessed transcript(s).  Claude backend "
          f"({OPUS_MODEL} manager + {HAIKU_MODEL}×{HAIKU_WORKERS} workers).")
    for t in pending:
        process_transcript(t, force=force, knowledge_dir=knowledge_dir)

    # Pass D: connection analysis across all sources. Uses the (patched) LLM layer.
    print("\n🔗 Pass D — analyzing connections across all nodes (Haiku parallel)...")
    _run_connect_pass(knowledge_dir=knowledge_dir)
    print(f"\n  📊 {_cost_summary()}")


def _run_connect_pass(knowledge_dir: Path | None = None):
    """Wrapped Pass D that respects knowledge_dir override."""
    _saved = None
    if knowledge_dir is not None:
        _saved = {
            "KNOWLEDGE_DIR":    P.KNOWLEDGE_DIR,
            "TOPICS_DIR":       P.TOPICS_DIR,
            "SOURCES_FILE":     P.SOURCES_FILE,
            "CONNECTIONS_FILE": P.CONNECTIONS_FILE,
            "INDEX_FILE":       P.INDEX_FILE,
            "CHROMA_DIR":       P.CHROMA_DIR,
        }
        P.KNOWLEDGE_DIR    = knowledge_dir
        P.TOPICS_DIR       = knowledge_dir / "topics"
        P.SOURCES_FILE     = knowledge_dir / "sources.json"
        P.CONNECTIONS_FILE = knowledge_dir / "connections.json"
        P.INDEX_FILE       = knowledge_dir / "_index.md"
        P.CHROMA_DIR       = knowledge_dir / ".chroma"
    try:
        P.run_connection_pass()
    finally:
        if _saved:
            for k, v in _saved.items():
                setattr(P, k, v)


def do_rebuild(knowledge_dir: Path | None = None):
    """Wipe ChromaDB + topic notes + connections + sources, then reprocess all."""
    kdir = knowledge_dir or P.KNOWLEDGE_DIR
    chroma_dir = (knowledge_dir / ".chroma") if knowledge_dir else P.CHROMA_DIR
    topics_dir = (knowledge_dir / "topics") if knowledge_dir else P.TOPICS_DIR
    conn_file = (knowledge_dir / "connections.json") if knowledge_dir else P.CONNECTIONS_FILE
    src_file = (knowledge_dir / "sources.json") if knowledge_dir else P.SOURCES_FILE

    print("🗑  Clearing ChromaDB index...")
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)

    if topics_dir.exists():
        for f in topics_dir.glob("*.md"):
            f.unlink()
        print("   cleared knowledge/topics/")

    conn_file.parent.mkdir(parents=True, exist_ok=True)
    P.save_json(conn_file, {"per_topic": {}, "edges": []})

    if src_file.exists():
        bak = src_file.with_suffix(".json.bak")
        src_file.rename(bak)
        print(f"   sources.json backed up to {bak.name}")

    process_all(force=True, knowledge_dir=knowledge_dir)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(1)

    # Parse --knowledge-dir DIR (optional). Everything else is passed to the sub-command.
    knowledge_dir: Path | None = None
    if "--knowledge-dir" in argv:
        idx = argv.index("--knowledge-dir")
        knowledge_dir = Path(argv[idx + 1]).expanduser().resolve()
        del argv[idx:idx + 2]

    if not argv:
        print("❌ --knowledge-dir requires a following command (e.g. --all, a file path).")
        sys.exit(1)

    arg = argv[0]

    # Wire Claude backend into process.py.
    install_claude_backend()

    if arg == "--rebuild-index":
        if knowledge_dir:
            _saved = {
                "KNOWLEDGE_DIR":  P.KNOWLEDGE_DIR,
                "TOPICS_DIR":     P.TOPICS_DIR,
                "SOURCES_FILE":   P.SOURCES_FILE,
                "INDEX_FILE":     P.INDEX_FILE,
            }
            P.KNOWLEDGE_DIR = knowledge_dir
            P.TOPICS_DIR    = knowledge_dir / "topics"
            P.SOURCES_FILE  = knowledge_dir / "sources.json"
            P.INDEX_FILE    = knowledge_dir / "_index.md"
        P.rebuild_index()
        print(f"✅ Index rebuilt.")
        return

    P.check_ollama()  # our patched version — embed only

    if arg == "--rebuild":
        do_rebuild(knowledge_dir=knowledge_dir)
    elif arg == "--all":
        process_all(knowledge_dir=knowledge_dir)
    elif arg == "--connect":
        _run_connect_pass(knowledge_dir=knowledge_dir)
        print(f"\n  📊 {_cost_summary()}")
    else:
        p = Path(arg)
        if not p.exists():
            alt = P.TRANSCRIPTS_DIR / p.name
            if alt.exists():
                p = alt
            else:
                print(f"❌ File not found: {arg}")
                sys.exit(1)
        process_transcript(p, knowledge_dir=knowledge_dir)
        print(f"\n  📊 {_cost_summary()}")


if __name__ == "__main__":
    main()
