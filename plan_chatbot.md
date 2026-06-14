# Scribe Chatbot — Plan (sub-agent B)

Owns: `server.py`, `graph/index.html`, `graph/graph.js`, the `google-genai` line in `setup.sh`.
Does NOT touch A's files (`process.py`, `export_graph.py`, `knowledge/`, `evals/`, `README.md`, `SETUP.md`).

## Task 1a — "stuck after one question" root cause + fix

**Root cause.** The server responds to `POST /api/chat` with `Connection: keep-alive` and HTTP/1.1, streaming SSE with no `Content-Length` and no chunked framing that the client can use to detect end-of-body. After the server's handler returns, `ThreadingHTTPServer` keeps the TCP socket open for keep-alive reuse. On the client, `res.body.getReader().read()` therefore **never resolves `done`** — the `while` loop never breaks, the `finally{}` that resets `chatBusy = false` never runs, so the second `sendChat()` early-returns on `if (chatBusy) return`. The visible symptom is exactly "stuck after one question."

**Fix (two layers, defensive):**
1. Server: send `Connection: close` so the socket is closed when the handler returns, making the client reader resolve `done`. Also set `self.close_connection = True` and `protocol_version` stays HTTP/1.0-style close. This is the real fix.
2. Client: treat the `{"type":"done"}` SSE event as the authoritative end-of-turn — break the read loop and finalize on `done` rather than relying solely on `reader.read()` resolving `done`. Belt-and-suspenders so a hung socket can never wedge the UI. Also `reader.cancel()` in `finally`.

N sequential questions then work because each turn closes its socket and each `done` resets state.

## Task 1b — Markdown renderer rewrite

**Current bugs in `renderMarkdown()`:**
- Double-escaping: the paragraph path escapes then `inline()` re-checks `&lt;`/`&amp;` and the `<br/>` literal it injects gets escaped on some paths → literal `<br/>` / `&gt;` shown.
- `_italic_` rule `(^|[^_])_([^_]+)_` fires inside `the_lazy_way_i_make_money_with_ai_2026.txt`, breaking filenames into italics.
- No heading (`##`) or blockquote (`>`) support — notes use both.

**New renderer** (shared by node descriptions and chat). Pipeline:
1. `escapeHtml()` once on raw text up front (so all downstream string ops operate on escaped text and never re-escape).
2. Split into blocks on blank lines. Classify each block:
   - all lines `>` → `<blockquote>` (strip leading `>`, render inner inline).
   - starts with `#{1,6} ` → `<h3>`/`<h4>` (clamp; notes use `##`/`###`).
   - all non-blank lines match `^[-*] ` → `<ul><li>`.
   - `---` alone → `<hr/>`.
   - else `<p>` with single `\n` → `<br/>`.
3. Inline pass (on already-escaped text): `` `code` `` → `<code>`, `**bold**` → `<strong>`, and italics ONLY for `*...*` (asterisk form). **Underscore italics are dropped entirely** — too dangerous given filenames; notes use `**` for emphasis anyway.
4. Source-shorthand substitution (task 2) runs in the inline pass after escaping.

Verified against `knowledge/topics/lazy_system.md`: no literal `<br/>`/`&gt;`, filenames intact.

## Task 2 — Source shorthand

Long filenames like `the_lazy_way_i_make_money_with_ai_2026.txt` are replaced at RENDER time. Pipeline keeps full filenames.

- On boot, build `sourceLabels`: from graph nodes where `plugin === null` (source nodes). For each filename, derive a deterministic short bold label: strip `.txt`, drop trailing 4-digit year, split on `_`, drop stopwords (`the,a,an,i,to,with,of,my,way,how`), take first 2–3 salient words, Title-Case → e.g. `the_lazy_way_i_make_money_with_ai_2026.txt` → **Lazy Money AI**. Guarantee uniqueness by suffixing.
- Substitution targets two forms in escaped text:
  - citation form `[file.txt § "Section"]` → `[**Label** § "Section"]` (keeps the section).
  - bare `file.txt` → clickable bold label.
- Rendered as a clickable element pointing at the source node (its id IS the filename) so clicking opens the source node in the detail panel. Reuse the existing `node-link` mechanism with `data-node="<full filename>"`.

## Task 3 — Gemini migration

`google-genai` v2.8.0 confirmed installed. Verified `client.models.generate_content_stream` exists; `GenerateContentConfig` takes `system_instruction`, `temperature`, `max_output_tokens`.

- Retrieval stays 100% local: Ollama `nomic-embed-text` query embedding + Chroma `facts`/`chunks` query + `nodes` SSE event unchanged.
- Generation: `from google import genai` / `from google.genai import types`. `client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])`. Model `gemini-2.5-flash`, fall back to `gemini-2.0-flash` on error. Stream:
  ```python
  for chunk in client.models.generate_content_stream(
      model=MODEL, contents=prompt,
      config=types.GenerateContentConfig(
          system_instruction=_SYSTEM, temperature=0.2, max_output_tokens=1024)):
      if chunk.text: sse token
  ```
- Missing `GEMINI_API_KEY` → SSE `error` telling the user to `export GEMINI_API_KEY=...`. API errors → SSE `error` with the message; try fallback model once.
- System prompt keeps intent: answer only from context, cite sources, wrap topics in `[[ ]]`. Drop the qwen `/no_think` prefix.
- `setup.sh` pip line gains `google-genai`.

## Loading animation

Three visible phases in the assistant bubble:
1. **Searching** — existing pulsing dot "Searching knowledge base…" (retrieval).
2. **Consulted N topics** — on `nodes` event (existing).
3. **Generating** — NEW: between `nodes` and the first `token`, show an animated "Thinking…" shimmer with bouncing dots inside the message body. Removed once the first token arrives; thereafter the typing cursor shows. CSS: `@keyframes` shimmer gradient + staggered bouncing dots.
