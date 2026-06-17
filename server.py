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

_SYSTEM = (
    "You are Scribe, a retrieval assistant answering ONLY from the user's "
    "personal knowledge base. Use the provided context passages and facts as "
    "ground truth. Rules:\n"
    "- Answer concisely and specifically, grounded in the context.\n"
    "- If the context does not contain the answer, say you don't have that in "
    "your knowledge base. Never invent facts.\n"
    "- Cite source filenames inline when useful.\n"
    "- When you reference one of the listed topics, wrap its name in [[double "
    "brackets]] so the UI can link it."
)

# ── Module-level Gemini backend state ────────────────────────────────────────
# Tracks whether Gemini is in a rate-limit cooldown so the UI can show a live
# countdown.  All fields are written under the GIL (CPython) — no explicit lock
# needed for simple reads/writes.

_gemini_cooldown_until: float = 0.0   # epoch seconds; 0 means not in cooldown
_gemini_retry_known: bool = False      # True when we parsed a real retry delay
_gemini_last_backend: str | None = None  # "gemini" | "qwen" | None


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
    _gemini_cooldown_until = 0.0
    _gemini_retry_known = False
    _gemini_last_backend = "gemini"


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
    }


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _topic_display(topic):
    """Match the graph node id produced by export_graph.py (slug → Title Case)."""
    return _slug(topic).replace("_", " ").title()


def retrieve_structured(query, n_facts=RAG_N_FACTS, n_chunks=RAG_N_CHUNKS,
                        max_topics=RAG_MAX_TOPICS):
    """Wider RAG used by the copy-paste view. Returns
        {topics: [...], sources: [{name, passages: [{section_title, text}], facts: [...]}]}
    grouped by source filename, in retrieval-relevance order.
    """
    import chromadb, ollama
    from collections import OrderedDict

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts_col  = client.get_collection("facts")
    chunks_col = client.get_collection("chunks")

    q_emb = ollama.embeddings(model=EMBED_MODEL, prompt=query)["embedding"]

    def _q(col, n):
        c = col.count()
        if c == 0:
            return {"documents": [[]], "metadatas": [[]]}
        return col.query(query_embeddings=[q_emb], n_results=min(n, c))

    fres = _q(facts_col, n_facts)
    cres = _q(chunks_col, n_chunks)

    fact_docs, fact_metas = fres["documents"][0], fres["metadatas"][0]
    chunk_docs, chunk_metas = cres["documents"][0], cres["metadatas"][0]

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

    # Attach video_summary from knowledge/sources.json so the Copy view can
    # surface per-source summary cards (and clipboard-copy retains full claim
    # block).  Lazily load once per call; the file is small.
    try:
        src_meta = json.loads((SCRIPT_DIR / "knowledge" / "sources.json").read_text())
    except Exception:
        src_meta = {}
    sources = []
    for s, blk in by_src.items():
        meta = src_meta.get(s, {})
        sources.append({
            "name": s,
            "title": meta.get("title", ""),
            "video_summary": meta.get("video_summary", ""),
            "url": meta.get("url", ""),
            **blk,
        })
    return {"topics": topics, "sources": sources}


def retrieve(query):
    """Return (consulted_topic_names, context_block). Raises on backend failure."""
    import chromadb, ollama

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts_col  = client.get_collection("facts")
    chunks_col = client.get_collection("chunks")

    q_emb = ollama.embeddings(model=EMBED_MODEL, prompt=query)["embedding"]

    def _query(col, n):
        count = col.count()
        if count == 0:
            return {"documents": [[]], "metadatas": [[]]}
        return col.query(query_embeddings=[q_emb], n_results=min(n, count))

    fres = _query(facts_col, N_FACTS)
    cres = _query(chunks_col, N_CHUNKS)

    fact_docs  = fres["documents"][0]
    fact_metas = fres["metadatas"][0]
    chunk_docs  = cres["documents"][0]
    chunk_metas = cres["metadatas"][0]

    # Consulted topics, in relevance order, deduped → graph node ids.
    # facts metadata carries a single canonical `topic` (see process.py schema).
    topics, seen = [], set()
    for meta in fact_metas:
        t = meta.get("topic")
        if not t:
            continue
        disp = _topic_display(t)
        if disp and disp.lower() not in seen:
            seen.add(disp.lower())
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
    for i, (s, blk) in enumerate(by_src.items(), 1):
        parts.append(f"===== SOURCE {i}: {s} =====")
        if blk["passages"]:
            parts.append("Passages:")
            for title, doc in blk["passages"]:
                hdr = f"[§ {title}] " if title else ""
                parts.append(f"{hdr}{doc}")
        if blk["facts"]:
            parts.append("\nKey facts from this source:")
            parts.extend(f"- {f}" for f in blk["facts"])
        parts.append("")  # blank line between sources

    return topics, "\n".join(parts).strip()


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
    return (f"Context from the knowledge base:\n\n{context}\n\n"
            f"Question: {query}\n\nAnswer:")


def generate_stream(client, query, context):
    """Yield generated text chunks from Gemini, trying the primary model then
    the fallback. Raises on hard failure (after the fallback also fails)."""
    from google.genai import types

    prompt = build_prompt(query, context)

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
                model=model, contents=prompt, config=config)
            for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    yield text
            return  # success
        except Exception as e:  # noqa: BLE001 — try the fallback model next
            last_err = e
            continue
    raise RuntimeError(str(last_err) if last_err else "Gemini generation failed.")


def qwen_stream(query, context):
    """Local fallback generation — stream from Ollama qwen3:1.7b. Used when Gemini
    is rate-limited or unavailable so the chat still answers."""
    import ollama
    import re as _re
    prompt = build_prompt(query, context)
    stream = ollama.chat(
        model=QWEN_MODEL,
        messages=[{"role": "system", "content": "/no_think\n" + _SYSTEM},
                  {"role": "user",   "content": prompt}],
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
        except json.JSONDecodeError:
            query = ""
            gemini_only = False

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
            topics, context = retrieve(query)
        except Exception as e:
            self._sse({"type": "error",
                       "message": f"Knowledge base unavailable ({e}). "
                                  f"Is Ollama running and has updateDB.sh been run?"})
            self._sse({"type": "done"})
            return

        self._sse({"type": "nodes", "nodes": topics})

        # ── Debug round-trip (Dev panel): the exact strings we send ──
        self._sse({"type": "debug",
                   "system": _SYSTEM,
                   "context": context,
                   "prompt": build_prompt(query, context)})

        if not context:
            self._sse({"type": "token",
                       "text": "I don't have anything in my knowledge base yet. "
                               "Run `updateDB.sh` to process some transcripts first."})
            self._sse({"type": "done"})
            return

        # ── Generate: prefer Gemini, fall back to local qwen unless gemini_only ──
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
                    for tok in qwen_stream(query, context):
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
                for tok in generate_stream(client, query, context):
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
                for tok in qwen_stream(query, context):
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
