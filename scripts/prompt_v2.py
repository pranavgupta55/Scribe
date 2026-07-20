"""prompt_v2 — Assemble the LLM prompt from a graph-augmented retrieval output.

This module turns the `retrieval` dict produced by `retrieval_v2.py` into a
(system_prompt, user_message) pair formatted per Anthropic's canonical XML
long-context pattern. The prompt is model-agnostic but tuned for Gemini 2.5
Flash (primary) with Claude / local fallbacks — XML-tag prompts work well on
both.

Data flow
---------
retrieval_v2.retrieve(query) -> retrieval dict
    -> assemble_prompt(query, retrieval)
        -> (SYSTEM_PROMPT_V2, "<retrieved_context>...</retrieved_context>\\n\\n<user_query>...</user_query>")

The retrieval dict schema is:
    {
      "sub_queries": [str, ...],
      "facts": [
          {
            "node_id": str,
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
      "meta": {...},
    }

Each `neighbor` dict has: node_id, text, kind, weight, confidence, source (optional).

Ordering rules
--------------
- Facts: sorted by descending `similarity`.
- Expansion kinds within a fact: fixed sequence
  (contradicts, agrees, builds_on, hosts, illustrates, practices).
  Empty kinds are omitted entirely.
- Chunks: non-side-effect first (similarity desc), then side-effect
  (similarity desc). Index restarts at 1 within the chunks section.
- `<user_query>` appears LAST (per Anthropic long-context guidance).
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# System prompt (constant)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_V2 = """You are Scribe, a personal-knowledge-base assistant. You answer questions grounded in retrieved passages and structured facts from the user's own notes and video transcripts.

<greetings>
If the user message is a greeting ("hi", "hello"), social pleasantry, or a meta-question about you: respond briefly and conversationally in 1-2 sentences. Do NOT cite sources, use [[topic]] markup, or emit any XML blocks. Ignore the retrieved context — it is irrelevant for these.
</greetings>

<answering>
For substantive questions, use the retrieved context as ground truth. Follow these rules:

1. **Contradictions first.** If any <contradicts> tag appears in the retrieved context, output a <conflicts> block FIRST — one line per pair, format: `Source A ("<short quote>") vs. Source B ("<short quote>")`. Do not average, reconcile, or hide contradictions. Only after listing all conflicts do you begin the actual answer.

2. **Inline citations.** Cite every substantive factual claim with `[F<n>]` for facts or `[C<n>]` for chunks, matching the `index` attribute in the retrieved context. Multiple citations: `[F1, F3]`.

3. **Weight higher-signal edges.** Contradictions (weight 1.0) and agreements (0.95) carry the most conceptual weight. Builds-on (0.8), hosts/illustrates/practices (0.5) provide scaffolding. Never drop a contradiction because of low weight.

4. **Anchor factual claims.** State directly-supported claims plainly. Flag partial ones with "though not stated explicitly," or "the passages hint at this." Flag synthesis with "combining these two:" or "my inference:".

5. **Prefer paraphrase.** Quote directly only when wording matters (definitions, exact numbers, specific instructions).

6. **Use topic markup navigably.** Wrap high-signal topic names in [[double brackets]] where the reader might want to explore further — but only when the topic adds navigation value.
</answering>"""


# ---------------------------------------------------------------------------
# Constants: ordering + truncation
# ---------------------------------------------------------------------------

# Fixed emission order for expansion kinds inside a <fact>.
_KIND_ORDER: tuple[str, ...] = (
    "contradicts",
    "agrees",
    "builds_on",
    "hosts",
    "illustrates",
    "practices",
)

# Per-kind inner tag name (semantic label for the neighbor's text body).
_KIND_INNER_TAG: dict[str, str] = {
    "contradicts": "claim",
    "agrees": "claim",
    "builds_on": "claim",
    "hosts": "concept",
    "illustrates": "example",
    "practices": "practice",
}

# Truncation limits (in characters).
_LIMIT_REGULAR = 1200
_LIMIT_CONTRADICTS = 2000
_LIMIT_CHUNK_BODY = 2500
_CHUNK_SENTENCE_WINDOW = 100  # look back this far for a sentence boundary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_xml_text(s: str) -> str:
    """Escape the three XML-critical chars in element text.

    Apostrophes and double-quotes are valid inside element text and are left
    untouched (they are only special inside attribute values, and we never
    interpolate untrusted data into attribute values).
    """
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int, *, sentence_boundary: bool = False) -> str:
    """Truncate `text` to `limit` chars, appending ' ... [truncated]' if cut.

    If `sentence_boundary` is True, search backward up to
    `_CHUNK_SENTENCE_WINDOW` chars for a '.', '!' or '?' and cut there for a
    cleaner break. Used for chunk bodies.
    """
    if not text or len(text) <= limit:
        return text
    cut = limit
    if sentence_boundary:
        window_start = max(0, limit - _CHUNK_SENTENCE_WINDOW)
        best = -1
        for i in range(limit - 1, window_start - 1, -1):
            if text[i] in ".!?":
                best = i + 1  # include the punctuation
                break
        if best > 0:
            cut = best
    return text[:cut].rstrip() + " ... [truncated]"


def _fmt_similarity(sim: Any) -> str:
    """Format a similarity float to 2 decimal places (as a string)."""
    try:
        return f"{float(sim):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_weight(w: Any) -> str:
    """Format an edge weight to 2 decimal places."""
    try:
        return f"{float(w):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_confidence(c: Any) -> str:
    """Format an edge confidence to 2 decimal places."""
    try:
        return f"{float(c):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _attr(name: str, value: Any) -> str:
    """Render one XML attribute if value is non-empty, else empty string.

    Value is escaped for attribute context (quotes + XML specials).
    """
    if value is None or value == "":
        return ""
    s = str(value)
    s = (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f' {name}="{s}"'


def _indent(level: int) -> str:
    """Return 2-space indent for `level` nesting depth."""
    return "  " * level


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

def _emit_neighbor(kind: str, neighbor: dict, level: int) -> list[str]:
    """Emit one <kind> element for an expansion neighbor.

    Returns a list of lines (no trailing newline on each).
    """
    inner_tag = _KIND_INNER_TAG.get(kind, "claim")
    text = neighbor.get("text", "") or ""
    limit = _LIMIT_CONTRADICTS if kind == "contradicts" else _LIMIT_REGULAR
    text = _truncate(text, limit)
    text = _escape_xml_text(text)

    attrs = (
        _attr("weight", _fmt_weight(neighbor.get("weight")))
        + _attr("confidence", _fmt_confidence(neighbor.get("confidence")))
        + _attr("node_id", neighbor.get("node_id"))
        + _attr("source", neighbor.get("source"))
    )

    pad = _indent(level)
    inner_pad = _indent(level + 1)
    lines = [f"{pad}<{kind}{attrs}>"]
    if text:
        lines.append(f"{inner_pad}<{inner_tag}>{text}</{inner_tag}>")
    lines.append(f"{pad}</{kind}>")
    return lines


def _emit_fact(index: int, fact: dict, level: int) -> list[str]:
    """Emit one <fact index="N" ...> element."""
    attrs = (
        _attr("index", index)
        + _attr("node_id", fact.get("node_id"))
        + _attr("topic", fact.get("topic"))
        + _attr("level", fact.get("level"))
        + _attr("source_count", fact.get("source_count"))
        + _attr("similarity", _fmt_similarity(fact.get("similarity")))
    )

    pad = _indent(level)
    inner_pad = _indent(level + 1)
    lines: list[str] = [f"{pad}<fact{attrs}>"]

    claim = _truncate(fact.get("text", "") or "", _LIMIT_REGULAR)
    claim = _escape_xml_text(claim)
    if claim:
        lines.append(f"{inner_pad}<claim>{claim}</claim>")

    expansions = fact.get("expansions") or {}
    for kind in _KIND_ORDER:
        neighbors = expansions.get(kind) or []
        if not neighbors:
            continue
        for nb in neighbors:
            lines.append("")  # blank line before each neighbor block
            lines.extend(_emit_neighbor(kind, nb, level + 1))

    lines.append(f"{pad}</fact>")
    return lines


def _emit_chunk(index: int, chunk: dict, level: int) -> list[str]:
    """Emit one <chunk index="N" ...> element."""
    is_side = bool(chunk.get("is_side_effect"))
    attrs = (
        _attr("index", index)
        + _attr("chunk_id", chunk.get("chunk_id"))
        + _attr("source", chunk.get("source"))
        + _attr("section_idx", chunk.get("section_idx"))
        + _attr("similarity", _fmt_similarity(chunk.get("similarity")))
    )
    if is_side:
        attrs += _attr("is_side_effect", "true")

    pad = _indent(level)
    inner_pad = _indent(level + 1)
    lines: list[str] = [f"{pad}<chunk{attrs}>"]

    title = _escape_xml_text(_truncate(chunk.get("section_title", "") or "", _LIMIT_REGULAR))
    if title:
        lines.append(f"{inner_pad}<title>{title}</title>")

    premise = _escape_xml_text(_truncate(chunk.get("premise", "") or "", _LIMIT_REGULAR))
    if premise:
        lines.append(f"{inner_pad}<premise>{premise}</premise>")

    body = _truncate(chunk.get("text", "") or "", _LIMIT_CHUNK_BODY, sentence_boundary=True)
    body = _escape_xml_text(body)
    if body:
        lines.append(f"{inner_pad}<body>{body}</body>")

    conclusion = _escape_xml_text(_truncate(chunk.get("conclusion", "") or "", _LIMIT_REGULAR))
    if conclusion:
        lines.append(f"{inner_pad}<conclusion>{conclusion}</conclusion>")

    lines.append(f"{pad}</chunk>")
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assemble_prompt(query: str, retrieval: dict) -> tuple[str, str]:
    """Assemble (system_prompt, user_message) from a retrieval dict.

    The user_message contains a `<retrieved_context>` block followed by a
    `<user_query>` block (query at the bottom per Anthropic long-context
    guidance).
    """
    facts = list(retrieval.get("facts") or [])
    chunks = list(retrieval.get("chunks") or [])

    # Facts: sort by similarity descending. Missing similarity treated as 0.
    facts_sorted = sorted(
        facts,
        key=lambda f: float(f.get("similarity") or 0.0),
        reverse=True,
    )

    # Chunks: non-side-effect first (similarity desc), then side-effect
    # (similarity desc).
    non_side = sorted(
        (c for c in chunks if not c.get("is_side_effect")),
        key=lambda c: float(c.get("similarity") or 0.0),
        reverse=True,
    )
    side = sorted(
        (c for c in chunks if c.get("is_side_effect")),
        key=lambda c: float(c.get("similarity") or 0.0),
        reverse=True,
    )
    chunks_ordered = non_side + side

    # Build the retrieved_context block.
    body_blocks: list[list[str]] = []
    for i, fact in enumerate(facts_sorted, start=1):
        body_blocks.append(_emit_fact(i, fact, level=1))
    for i, chunk in enumerate(chunks_ordered, start=1):
        body_blocks.append(_emit_chunk(i, chunk, level=1))

    inner = "\n\n".join("\n".join(block) for block in body_blocks)
    if inner:
        retrieved_context = f"<retrieved_context>\n{inner}\n</retrieved_context>"
    else:
        retrieved_context = "<retrieved_context>\n</retrieved_context>"

    # User query at the bottom (escape only the three XML specials).
    q_escaped = _escape_xml_text(query or "")
    user_query_block = f"<user_query>\n{q_escaped}\n</user_query>"

    user_message = f"{retrieved_context}\n\n{user_query_block}"

    # Strip trailing whitespace on any line (belt-and-braces).
    user_message = "\n".join(line.rstrip() for line in user_message.split("\n"))

    return SYSTEM_PROMPT_V2, user_message


def compute_prompt_stats(retrieval: dict) -> dict:
    """Return observability stats about the assembled prompt payload."""
    # Re-assemble with an empty query to measure the retrieval-side payload,
    # then correct for the empty query block by measuring against a real call.
    # For 'total_chars' we take the full user_message including an empty query.
    _, user_message = assemble_prompt("", retrieval)

    facts = list(retrieval.get("facts") or [])
    chunks = list(retrieval.get("chunks") or [])

    n_by_kind: dict[str, int] = {kind: 0 for kind in _KIND_ORDER}
    for f in facts:
        exp = f.get("expansions") or {}
        for kind in _KIND_ORDER:
            n_by_kind[kind] += len(exp.get(kind) or [])

    return {
        "total_chars": len(user_message),
        "n_facts": len(facts),
        "n_chunks": len(chunks),
        "n_expansions_by_kind": n_by_kind,
        "n_contradictions": n_by_kind["contradicts"],
    }


# ---------------------------------------------------------------------------
# Self-test (`python3 scripts/prompt_v2.py --test`)
# ---------------------------------------------------------------------------

def _run_self_test() -> None:
    """Run assertions against hand-crafted retrieval fixtures. Exits nonzero on failure."""
    import sys

    def _fail(msg: str) -> None:
        print(f"FAIL: {msg}")
        sys.exit(1)

    # --- Test 1: empty retrieval ---------------------------------------
    empty: dict = {"sub_queries": [], "facts": [], "chunks": [], "meta": {}}
    _, msg = assemble_prompt("what is focus?", empty)
    if "<retrieved_context>" not in msg or "</retrieved_context>" not in msg:
        _fail("Test 1: missing <retrieved_context> block")
    if "<user_query>" not in msg or "</user_query>" not in msg:
        _fail("Test 1: missing <user_query> block")
    if msg.rfind("<user_query>") < msg.rfind("</retrieved_context>"):
        _fail("Test 1: <user_query> should come AFTER </retrieved_context>")

    # --- Test 2: 1 fact + 1 contradicts + 1 chunk ----------------------
    r2 = {
        "sub_queries": ["q"],
        "facts": [
            {
                "node_id": "claim:1",
                "text": "A focused founder wins.",
                "topic": "Focus",
                "level": "L2",
                "source_count": 2,
                "similarity": 0.9,
                "sub_query_indices": [0],
                "expansions": {
                    "contradicts": [
                        {
                            "node_id": "claim:99",
                            "text": "Diversified founders win.",
                            "kind": "contradicts",
                            "weight": 1.0,
                            "confidence": 1.0,
                            "source": None,
                        }
                    ],
                    "agrees": [],
                    "builds_on": [],
                    "hosts": [],
                    "illustrates": [],
                    "practices": [],
                },
            }
        ],
        "chunks": [
            {
                "chunk_id": "vid__s0",
                "source": "vid",
                "section_idx": 0,
                "section_title": "Intro",
                "premise": "opens with…",
                "conclusion": "concludes with…",
                "text": "body body body",
                "similarity": 0.7,
                "sub_query_indices": [0],
                "is_side_effect": False,
            }
        ],
        "meta": {},
    }
    _, m2 = assemble_prompt("q", r2)
    for needle in ("<contradicts", '<fact index="1"', '<chunk index="1"', "<user_query>"):
        if needle not in m2:
            _fail(f"Test 2: missing {needle!r} in output")

    # --- Test 3: 2 facts, one with all-empty expansions ----------------
    r3 = {
        "facts": [
            {
                "node_id": "claim:1",
                "text": "Fact one.",
                "topic": "T1",
                "level": "L2",
                "source_count": 1,
                "similarity": 0.9,
                "expansions": {k: [] for k in _KIND_ORDER},
            },
            {
                "node_id": "claim:2",
                "text": "Fact two.",
                "topic": "T2",
                "level": "L2",
                "source_count": 1,
                "similarity": 0.8,
                "expansions": {
                    "contradicts": [],
                    "agrees": [
                        {
                            "node_id": "claim:20",
                            "text": "Agrees text.",
                            "kind": "agrees",
                            "weight": 0.95,
                            "confidence": 1.0,
                        }
                    ],
                    "builds_on": [],
                    "hosts": [],
                    "illustrates": [],
                    "practices": [],
                },
            },
        ],
        "chunks": [],
    }
    _, m3 = assemble_prompt("q", r3)
    # Split around fact 1 and fact 2
    f1_start = m3.find('<fact index="1"')
    f1_end = m3.find("</fact>", f1_start)
    fact1_block = m3[f1_start:f1_end]
    if any(
        f"<{kind}" in fact1_block for kind in _KIND_ORDER
    ):
        _fail("Test 3: fact1 with empty expansions still emitted an expansion tag")
    if "<claim>Fact one.</claim>" not in fact1_block:
        _fail("Test 3: fact1 claim missing")
    if "<agrees" not in m3:
        _fail("Test 3: fact2 <agrees> should still be emitted")

    # --- Test 4: side-effect chunk ordering ----------------------------
    r4 = {
        "facts": [],
        "chunks": [
            {
                "chunk_id": "vid__s0",
                "source": "vid",
                "section_idx": 0,
                "section_title": "primary",
                "premise": "",
                "conclusion": "",
                "text": "primary body",
                "similarity": 0.5,
                "is_side_effect": False,
            },
            {
                "chunk_id": "vid__s1",
                "source": "vid",
                "section_idx": 1,
                "section_title": "aside",
                "premise": "",
                "conclusion": "",
                "text": "side body",
                "similarity": 0.9,  # higher, but side-effect must still come after
                "is_side_effect": True,
            },
        ],
    }
    _, m4 = assemble_prompt("q", r4)
    if 'is_side_effect="true"' not in m4:
        _fail("Test 4: side-effect chunk missing is_side_effect attribute")
    if 'is_side_effect="false"' in m4:
        _fail("Test 4: is_side_effect should be omitted when False")
    primary_pos = m4.find("primary body")
    side_pos = m4.find("side body")
    if not (primary_pos < side_pos and primary_pos > 0 and side_pos > 0):
        _fail("Test 4: side-effect chunk should appear AFTER non-side-effect chunk")

    # --- Test 5: long claim truncation ---------------------------------
    long_text = "abcdefghij" * 250  # 2500 chars
    r5 = {
        "facts": [
            {
                "node_id": "claim:1",
                "text": long_text,
                "topic": "T",
                "level": "L2",
                "source_count": 1,
                "similarity": 0.9,
                "expansions": {k: [] for k in _KIND_ORDER},
            }
        ],
        "chunks": [],
    }
    _, m5 = assemble_prompt("q", r5)
    if "... [truncated]" not in m5:
        _fail("Test 5: long claim should have ' ... [truncated]' marker")
    # Extract the claim body and verify length
    claim_open = m5.find("<claim>")
    claim_close = m5.find("</claim>", claim_open)
    claim_body = m5[claim_open + len("<claim>") : claim_close]
    # 1200-char truncation + " ... [truncated]" suffix
    if len(claim_body) > 1200 + len(" ... [truncated]") + 5:
        _fail(f"Test 5: truncated claim too long ({len(claim_body)} chars)")
    if not claim_body.endswith("[truncated]"):
        _fail("Test 5: truncated claim should end with '[truncated]'")

    # --- Test 6: XML escaping ------------------------------------------
    r6 = {
        "facts": [
            {
                "node_id": "claim:1",
                "text": "<script>alert('xss')</script> & more",
                "topic": "T",
                "level": "L2",
                "source_count": 1,
                "similarity": 0.9,
                "expansions": {k: [] for k in _KIND_ORDER},
            }
        ],
        "chunks": [],
    }
    _, m6 = assemble_prompt("q", r6)
    if "<script>" in m6.replace("<claim>", "").replace("</claim>", ""):
        _fail("Test 6: raw <script> should be escaped")
    if "&lt;script&gt;" not in m6:
        _fail("Test 6: escaped '&lt;script&gt;' should appear")
    if "&amp; more" not in m6:
        _fail("Test 6: '&' should be escaped to '&amp;'")

    # --- Test 7: kind ordering (all six kinds) -------------------------
    def _nb(kind: str, i: int) -> dict:
        return {
            "node_id": f"{kind}:{i}",
            "text": f"{kind}-text-{i}",
            "kind": kind,
            "weight": 1.0,
            "confidence": 1.0,
        }

    r7 = {
        "facts": [
            {
                "node_id": "claim:1",
                "text": "root claim",
                "topic": "T",
                "level": "L2",
                "source_count": 1,
                "similarity": 0.9,
                "expansions": {
                    "practices": [_nb("practices", 1)],
                    "illustrates": [_nb("illustrates", 1)],
                    "hosts": [_nb("hosts", 1)],
                    "builds_on": [_nb("builds_on", 1)],
                    "agrees": [_nb("agrees", 1)],
                    "contradicts": [_nb("contradicts", 1)],
                },
            }
        ],
        "chunks": [],
    }
    _, m7 = assemble_prompt("q", r7)
    positions = {kind: m7.find(f"<{kind}") for kind in _KIND_ORDER}
    for kind, pos in positions.items():
        if pos < 0:
            _fail(f"Test 7: <{kind}> missing from output")
    expected_order = list(_KIND_ORDER)
    actual_order = sorted(_KIND_ORDER, key=lambda k: positions[k])
    if actual_order != expected_order:
        _fail(f"Test 7: kind order wrong. expected {expected_order}, got {actual_order}")

    # --- compute_prompt_stats sanity ----------------------------------
    stats = compute_prompt_stats(r7)
    if stats["n_facts"] != 1 or stats["n_chunks"] != 0:
        _fail(f"stats: unexpected fact/chunk counts: {stats}")
    if stats["n_contradictions"] != 1:
        _fail(f"stats: expected 1 contradiction, got {stats['n_contradictions']}")
    for kind in _KIND_ORDER:
        if stats["n_expansions_by_kind"][kind] != 1:
            _fail(f"stats: expected 1 {kind}, got {stats['n_expansions_by_kind'][kind]}")
    if stats["total_chars"] <= 0:
        _fail("stats: total_chars should be positive")

    print("OK")


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        _run_self_test()
    else:
        print("Usage: python3 scripts/prompt_v2.py --test")
        sys.exit(2)
