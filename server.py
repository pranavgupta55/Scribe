#!/usr/bin/env python3
"""
Scribe local server.

Serves the static graph viewer AND a RAG chat endpoint over the knowledge base.

  GET  /<anything>   → static files (graph viewer, etc.)
  POST /api/chat     → Server-Sent Events stream:
                         {"type":"nodes","nodes":[...]}   topics consulted
                         {"type":"token","text":"..."}    streamed answer
                         {"type":"done"}                  end of turn
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

N_FACTS  = 10    # facts retrieved (precise grounding + consulted-topic surfacing)
N_CHUNKS = 5     # full-context passages for the answer
MAX_TOPICS = 8

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


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _topic_display(topic):
    """Match the graph node id produced by export_graph.py (slug → Title Case)."""
    return _slug(topic).replace("_", " ").title()


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

    # Context block: full-context passages first, then precise facts
    parts = []
    if chunk_docs:
        parts.append("PASSAGES:")
        for doc, meta in zip(chunk_docs, chunk_metas):
            parts.append(f"[{meta.get('source', '?')}] {doc}")
    if fact_docs:
        parts.append("\nFACTS:")
        for doc, meta in zip(fact_docs, fact_metas):
            parts.append(f"- {doc}  [{meta.get('source', '?')}]")

    return topics, "\n".join(parts)


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


def generate_stream(client, query, context):
    """Yield generated text chunks from Gemini, trying the primary model then
    the fallback. Raises on hard failure (after the fallback also fails)."""
    from google.genai import types

    prompt = (f"Context from the knowledge base:\n\n{context}\n\n"
              f"Question: {query}\n\nAnswer:")
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        temperature=0.2,
        max_output_tokens=1024,
    )

    last_err = None
    for model in (GEMINI_MODEL, GEMINI_MODEL_FALLBACK):
        try:
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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SCRIPT_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass  # quiet

    def _sse(self, obj):
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            query = (body.get("query") or "").strip()
        except json.JSONDecodeError:
            query = ""

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

        if not context:
            self._sse({"type": "token",
                       "text": "I don't have anything in my knowledge base yet. "
                               "Run `updateDB.sh` to process some transcripts first."})
            self._sse({"type": "done"})
            return

        # ── Generate (streamed via Gemini) ──
        try:
            client = _gemini_client()
        except RuntimeError as e:
            # Missing API key — clear, actionable message.
            self._sse({"type": "error", "message": str(e)})
            self._sse({"type": "done"})
            return
        except ImportError:
            self._sse({"type": "error",
                       "message": "The google-genai SDK is not installed. "
                                  "Run: pip3 install google-genai"})
            self._sse({"type": "done"})
            return

        try:
            for tok in generate_stream(client, query, context):
                self._sse({"type": "token", "text": tok})
        except BrokenPipeError:
            return  # client navigated away
        except Exception as e:
            self._sse({"type": "error", "message": f"Gemini generation failed: {e}"})

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
