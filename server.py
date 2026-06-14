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
for full-context passages). Generation uses the same local qwen3:1.7b model.
"""

import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CHROMA_DIR  = SCRIPT_DIR / ".chroma"
PORT        = 8765

EXTRACT_MODEL = "qwen3:1.7b"
EMBED_MODEL   = "nomic-embed-text"

N_FACTS  = 10    # facts retrieved (precise grounding + consulted-topic surfacing)
N_CHUNKS = 5     # full-context passages for the answer
MAX_TOPICS = 8

_SYSTEM = (
    "/no_think\n"
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

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
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

        # ── Generate (streamed) ──
        try:
            import ollama
            prompt = (f"Context from the knowledge base:\n\n{context}\n\n"
                      f"Question: {query}\n\nAnswer:")
            stream = ollama.chat(
                model=EXTRACT_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                stream=True,
                options={"temperature": 0.2, "num_ctx": 8192, "num_predict": 800},
            )
            for part in stream:
                tok = part.get("message", {}).get("content", "")
                if tok:
                    self._sse({"type": "token", "text": tok})
        except BrokenPipeError:
            return  # client navigated away
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
