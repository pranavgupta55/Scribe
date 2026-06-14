#!/usr/bin/env python3
"""
Scribe knowledge pipeline (two-pass, whole-document).

Pass A — SEGMENT: the whole transcript is read in one LLM call to produce an
outline (5-10 sections, each with premise + conclusion) and a SMALL canonical
set of topic labels (4-8) for the entire video.

Pass B — EXTRACT: each section is processed individually to pull atomic, grounded
claims (tagged with one canonical topic), named entities, and subject-predicate-
object relationship triples — extracted directly from the text, never invented.

Storage:
  chunks — transcript SECTIONS (rich context for RAG retrieval)
  facts  — atomic CLAIMS (precise semantic search; one canonical topic each)
Topic notes (knowledge/topics/*.md) are assembled DETERMINISTICALLY from the
stored claims — no free-text synthesis call, so nothing is re-explained or
truncated.

Usage:
    python3 process.py <transcript.txt>    # process a single file
    python3 process.py --all               # process all unprocessed transcripts
    python3 process.py --rebuild           # clear ChromaDB and reprocess everything
    python3 process.py --rebuild-index     # regenerate knowledge/_index.md only
"""

import sys
import json
import re
import time
import shutil
from pathlib import Path
from datetime import datetime

SCRIPT_DIR      = Path(__file__).parent.resolve()
KNOWLEDGE_DIR   = SCRIPT_DIR / "knowledge"
TOPICS_DIR      = KNOWLEDGE_DIR / "topics"
SOURCES_FILE    = KNOWLEDGE_DIR / "sources.json"
CONNECTIONS_FILE= KNOWLEDGE_DIR / "connections.json"
INDEX_FILE      = KNOWLEDGE_DIR / "_index.md"
CHROMA_DIR      = SCRIPT_DIR / ".chroma"
TRANSCRIPTS_DIR = SCRIPT_DIR / "transcripts"

EXTRACT_MODEL = "qwen3:1.7b"
EMBED_MODEL   = "nomic-embed-text"

# Sectioning targets (Pass A)
MIN_SECTIONS = 5
MAX_SECTIONS = 10
MAX_TOPICS   = 10
MIN_TOPICS   = 1

# Cosine distance below which two claims are flagged as related/confirming
CONNECTION_THRESHOLD = 0.20

# Ollama option sets (num_ctx is set explicitly so input is never silently truncated)
SEGMENT_OPTS = {
    "num_ctx": 16384, "num_predict": 2048,
    "temperature": 0.2, "top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05,
}
EXTRACT_OPTS = {
    "num_ctx": 8192, "num_predict": 1536,
    "temperature": 0.2, "top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05,
}


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

def check_ollama():
    try:
        import ollama
        result = ollama.list()
        names = [m["model"] for m in result.get("models", [])]
        missing = [m for m in (EXTRACT_MODEL, EMBED_MODEL)
                   if not any(m.split(":")[0] in n for n in names)]
        if missing:
            print(f"❌ Missing Ollama model(s): {', '.join(missing)}")
            print(f"   Run: ollama pull {' && ollama pull '.join(missing)}")
            sys.exit(1)
    except Exception:
        print("❌ Ollama is not running.  Start it with: ollama serve")
        sys.exit(1)


# ---------------------------------------------------------------------------
# LLM prompts — thinking disabled via /no_think (kept compact for json mode)
# ---------------------------------------------------------------------------

_SEGMENT_SYSTEM = (
    "/no_think\n"
    "You are a precise transcript analyst. You read an entire video transcript and "
    "break it into its natural sections. You output ONLY valid JSON — no markdown, no "
    "commentary, no code fences. Every field must be grounded in what the transcript "
    "actually says. Do not add outside knowledge. Do not explain general concepts."
)

_SEGMENT_TMPL = """Here is the full transcript of a video.

TRANSCRIPT:
{transcript}

Break this video into its {min_s} to {max_s} natural sections in the order they occur.
For the whole video, also choose a SHORT list of {min_t} to {max_t} canonical topic
labels. Merge synonyms into ONE label (e.g. do not list both "Lead Generation" and
"Getting Leads"). Topic labels are 1-3 words, Title Case.

Return EXACTLY this JSON and nothing else:
{{
  "video_summary": "one sentence: what specific thing this video teaches or argues",
  "topics": ["Topic A", "Topic B"],
  "sections": [
    {{
      "title": "short section title",
      "start_marker": "first 8 words of this section, copied verbatim from the transcript",
      "premise": "the specific setup/claim the speaker makes to OPEN this section (one sentence, from the transcript)",
      "conclusion": "the specific takeaway the speaker lands on to CLOSE this section (one sentence, from the transcript)"
    }}
  ]
}}

Rules:
- premise and conclusion must be SPECIFIC to this video, quoting concrete numbers,
  names, and steps the speaker gives. Never write generic definitions.
- topics: max {max_t}, deduplicated, no near-synonyms.
- sections: between {min_s} and {max_s}.
"""

_EXTRACT_SYSTEM = (
    "/no_think\n"
    "You extract specific, factual knowledge from a transcript section. You output ONLY "
    "valid JSON — no markdown, no commentary, no code fences. Extract ONLY what is stated "
    "in the text. Never add general background, definitions, or explanations the speaker "
    "did not give. If the section contains no concrete claim, return empty arrays."
)

_EXTRACT_TMPL = """This is one section of a video titled "{title}".
The video's canonical topics are: {topic_list}.

SECTION TEXT:
{section}

Extract the SPECIFIC knowledge stated in this section.

Return EXACTLY this JSON and nothing else:
{{
  "claims": [
    {{
      "claim": "one specific, standalone, verifiable statement made in the text (include concrete numbers, names, steps)",
      "topic": "the single best-fitting label from the canonical topics above"
    }}
  ],
  "entities": [
    {{"name": "Name", "type": "person|org|product|tool|method|metric", "mention": "the exact phrase the transcript uses it in"}}
  ],
  "triples": [
    {{"subject": "X", "predicate": "verb phrase", "object": "Y"}}
  ]
}}

Rules:
- claims: atomic (one fact each), decontextualized (understandable alone), max 6.
  Each claim MUST be something THIS speaker actually said — quote the specifics.
- Do NOT write definitions of common terms. Do NOT explain what AI/marketing/etc. are.
- topic for each claim MUST be exactly one of: {topic_list}.
- entities: only things explicitly named in this section, max 6.
- triples: subject-predicate-object derived from the claims, max 6. Keep subject/object
  short (the actual named thing), predicate a short verb phrase.
"""


def _chat_json(system, user, options):
    """One Ollama chat call in JSON mode, thinking-stripped, parsed. None on failure."""
    import ollama
    for attempt in range(3):
        try:
            resp = ollama.chat(
                model=EXTRACT_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": user}],
                format="json",
                options=options,
            )
            raw = re.sub(r"<think>.*?</think>", "", resp["message"]["content"],
                         flags=re.DOTALL).strip()
            return json.loads(raw)
        except (json.JSONDecodeError, KeyError):
            if attempt == 2:
                return None
            time.sleep(1)


def llm_segment(full_text):
    """Pass A — whole transcript → {video_summary, topics, sections}."""
    user = _SEGMENT_TMPL.format(
        transcript=full_text,
        min_s=MIN_SECTIONS, max_s=MAX_SECTIONS,
        min_t=4, max_t=8,
    )
    data = _chat_json(_SEGMENT_SYSTEM, user, SEGMENT_OPTS) or {}

    topics = [t.strip() for t in data.get("topics", [])
              if isinstance(t, str) and t.strip()]
    # dedupe (case-insensitive), clamp
    seen, uniq = set(), []
    for t in topics:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    topics = uniq[:MAX_TOPICS]

    sections = [s for s in data.get("sections", []) if isinstance(s, dict)]
    if len(sections) > MAX_SECTIONS:
        sections = sections[:MAX_SECTIONS]

    return {
        "video_summary": (data.get("video_summary") or "").strip(),
        "topics": topics,
        "sections": sections,
    }


def llm_extract(section_text, section_title, topic_list):
    """Pass B — one section → {claims:[{claim,topic}], entities, triples}."""
    user = _EXTRACT_TMPL.format(
        title=section_title,
        topic_list=", ".join(topic_list) if topic_list else "(none)",
        section=section_text,
    )
    data = _chat_json(_EXTRACT_SYSTEM, user, EXTRACT_OPTS) or {}

    allowed = {t.lower(): t for t in topic_list}
    claims = []
    for c in data.get("claims", [])[:6]:
        if not isinstance(c, dict):
            continue
        text = (c.get("claim") or "").strip()
        if not text:
            continue
        topic = (c.get("topic") or "").strip()
        # snap topic to the canonical list; default to first topic if unknown
        topic = allowed.get(topic.lower())
        if not topic:
            topic = topic_list[0] if topic_list else "General"
        claims.append({"claim": text, "topic": topic})

    entities = [e for e in data.get("entities", []) if isinstance(e, dict)][:6]

    # Triples from a 1.7B model are noisy: drop echoed template placeholders and
    # malformed entries where a whole claim sentence leaked into subject/object.
    JUNK_PRED = {"verb phrase", "predicate", "", "is", "are", "was", "were"}
    triples = []
    for t in data.get("triples", []):
        if not isinstance(t, dict):
            continue
        s = str(t.get("subject", "")).strip()
        p = str(t.get("predicate", "")).strip()
        o = str(t.get("object", "")).strip()
        if not (s and p and o):
            continue
        if p.lower() in JUNK_PRED:
            continue
        if len(s.split()) > 6 or len(o.split()) > 6:
            continue
        triples.append({"subject": s, "predicate": p, "object": o})
    triples = triples[:6]

    return {"claims": claims, "entities": entities, "triples": triples}


# ---------------------------------------------------------------------------
# Section splitting — anchor on start_marker, fall back to equal slices
# ---------------------------------------------------------------------------

def _equal_slices(text, outline_sections):
    n = max(1, min(len(outline_sections), MAX_SECTIONS) or 1)
    words = text.split()
    per = max(1, len(words) // n)
    out = []
    for i in range(n):
        seg = words[i * per:] if i == n - 1 else words[i * per:(i + 1) * per]
        body = " ".join(seg).strip()
        if body:
            meta = outline_sections[i] if i < len(outline_sections) else {}
            out.append({**meta, "text": body})
    return out


def split_into_sections(text, outline_sections):
    """Split raw transcript into sections using each section's start_marker."""
    if not outline_sections:
        return _equal_slices(text, [{}])

    lower = text.lower()
    pairs = []
    for i, s in enumerate(outline_sections):
        marker = " ".join((s.get("start_marker") or "").split()[:8]).lower()
        idx = lower.find(marker) if marker else -1
        if idx != -1:
            pairs.append((i, idx))

    # keep strictly increasing anchor points
    inc, last = [], -1
    for i, idx in sorted(pairs, key=lambda p: p[1]):
        if idx > last:
            inc.append((i, idx))
            last = idx

    if len(inc) < 2:
        return _equal_slices(text, outline_sections)

    out = []
    for j, (i, idx) in enumerate(inc):
        start = idx if j > 0 else 0
        end = inc[j + 1][1] if j + 1 < len(inc) else len(text)
        body = text[start:end].strip()
        if body:
            out.append({**outline_sections[i], "text": body})
    return out


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

# nomic-embed-text has a 2048-token window; cap input so long sections don't 500.
EMBED_MAX_CHARS = 6000


def embed(text):
    import ollama
    return ollama.embeddings(model=EMBED_MODEL,
                             prompt=text[:EMBED_MAX_CHARS])["embedding"]


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def get_collections():
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    facts  = client.get_or_create_collection("facts",  metadata={"hnsw:space": "cosine"})
    chunks = client.get_or_create_collection("chunks", metadata={"hnsw:space": "cosine"})
    return facts, chunks


def safe_query(col, query_embeddings, n_results, where=None):
    """Query ChromaDB, guarding against empty collections and filter mismatches."""
    count = col.count()
    if count == 0:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    kwargs = {"query_embeddings": query_embeddings, "n_results": min(n_results, count)}
    if where:
        kwargs["where"] = where
    try:
        return col.query(**kwargs)
    except Exception:
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


# ---------------------------------------------------------------------------
# Knowledge file helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def topic_slug(topic):
    return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")


def update_topic_file(topic, facts_col):
    """Deterministically assemble a topic note from stored claims. No LLM call.

    Returns True if a note was written (topic had ≥1 claim), else False.
    """
    slug = topic_slug(topic)
    try:
        res = facts_col.get(where={"topic": topic},
                            include=["documents", "metadatas"])
    except Exception:
        return False

    docs  = res.get("documents") or []
    metas = res.get("metadatas") or []
    if not docs:
        return False

    claims = list(zip(docs, metas))

    # Headline (graph node description): shortest substantive claim, else first
    eligible = [d for d, _ in claims if len(d.split()) >= 8]
    headline = min(eligible, key=lambda d: len(d.split())) if eligible else docs[0]

    lines = [f"# {topic}", "", f"> {headline}", "", "## Claims"]
    sources_set = set()
    for d, m in claims:
        src  = m.get("source", "?")
        sect = m.get("section_title", "")
        sources_set.add(src)
        cite = f'[{src} § "{sect}"]' if sect else f"[{src}]"
        lines.append(f"- {d} — {cite}")

    # Relationships from triples — keep only those grounded in THIS topic's claims
    # (a section's triples are shared by all its claims, so filter by relevance to
    # avoid bleeding unrelated relationships into every topic).
    claims_blob = " ".join(d.lower() for d, _ in claims)
    triples, seen = [], set()
    for _, m in claims:
        try:
            for t in json.loads(m.get("triples", "[]") or "[]"):
                subj = t.get("subject", "")
                obj  = t.get("object", "")
                key = (subj.lower(), t.get("predicate", "").lower(), obj.lower())
                if not all(key) or key in seen:
                    continue
                if subj.lower() in claims_blob or obj.lower() in claims_blob:
                    seen.add(key)
                    triples.append(t)
        except (json.JSONDecodeError, TypeError):
            continue

    if triples:
        lines += ["", "## Relationships"]
        for t in triples[:8]:
            lines.append(f"- {t['subject']} → {t['predicate']} → {t['object']}")

    lines += [
        "",
        "---",
        f"_Topic appears in {len(sources_set)} source(s) · "
        f"{len(claims)} claim(s) · {len(triples)} relationship(s)_",
        f"_Sources: {', '.join(sorted(sources_set))}_",
        "",
    ]

    (TOPICS_DIR / f"{slug}.md").write_text("\n".join(lines))
    return True


def rebuild_index():
    sources = load_json(SOURCES_FILE, {})
    topic_files = sorted(TOPICS_DIR.glob("*.md")) if TOPICS_DIR.exists() else []

    lines = [
        "# Scribe Knowledge Index\n\n",
        f"_{len(sources)} source(s) · {len(topic_files)} topic(s)_\n\n",
        "## Topics\n\n",
    ]
    for tf in topic_files:
        name = tf.stem.replace("_", " ").title()
        lines.append(f"- [{name}](topics/{tf.name})\n")

    lines.append("\n## Sources\n\n")
    for fname, meta in sorted(sources.items()):
        topics_str = ", ".join(meta.get("topics", [])) or "—"
        claim_n   = meta.get("claim_count", meta.get("fact_count", "?"))
        section_n = meta.get("section_count", meta.get("chunk_count", "?"))
        summary   = meta.get("video_summary", "")
        line = (f"- **{fname}** — {claim_n} claims · {section_n} sections · "
                f"topics: {topics_str}")
        if summary:
            line += f"\n  - _{summary}_"
        lines.append(line + "\n")

    INDEX_FILE.write_text("".join(lines))


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_transcript(transcript_path, force=False):
    path = Path(transcript_path).resolve()
    name = path.name

    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    TOPICS_DIR.mkdir(exist_ok=True)

    sources = load_json(SOURCES_FILE, {})
    if name in sources and not force:
        print(f"  ℹ️  {name} already processed — skipping.")
        return

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"  ⚠️  {name} is empty — skipping.")
        return

    word_count = len(text.split())
    print(f"\n📄 {name} — {word_count} words")

    # ── Pass A: SEGMENT (whole transcript) ──
    print("  🧭 Pass A — segmenting whole transcript...", end="\r")
    outline = llm_segment(text)
    topics  = outline["topics"]
    sections = split_into_sections(text, outline["sections"])
    if MIN_TOPICS <= len(topics) <= MAX_TOPICS:
        pass
    else:
        print(f"  ⚠️  topic count {len(topics)} outside target — proceeding with {topics}")
    print(f"  🧭 Pass A — {len(sections)} sections · {len(topics)} canonical topics: "
          f"{', '.join(topics)}" + " " * 8)

    facts_col, chunks_col = get_collections()
    connections = load_json(CONNECTIONS_FILE, {"connections": []})

    used_topics = set()
    claim_count = 0
    entity_count = 0

    # ── Pass B: EXTRACT (per section) ──
    for i, sec in enumerate(sections):
        title = (sec.get("title") or f"Section {i + 1}").strip()
        body  = sec["text"]
        print(f"  [{i + 1:>2}/{len(sections)}] extracting · {title[:40]}...", end="\r")

        # Store the section as a retrieval chunk
        chunks_col.upsert(
            ids=[f"{name}__s{i}"],
            documents=[body],
            embeddings=[embed(body)],
            metadatas=[{
                "source":        name,
                "section_idx":   i,
                "section_title": title,
                "premise":       (sec.get("premise") or "")[:500],
                "conclusion":    (sec.get("conclusion") or "")[:500],
            }],
        )

        extraction = llm_extract(body, title, topics)
        triples_json = json.dumps(extraction["triples"])
        entity_count += len(extraction["entities"])

        for c_idx, claim in enumerate(extraction["claims"]):
            ctext  = claim["claim"]
            ctopic = claim["topic"]
            used_topics.add(ctopic)
            c_emb = embed(ctext)

            # Cross-source connection detection
            similar = safe_query(
                facts_col, query_embeddings=[c_emb], n_results=3,
                where={"source": {"$ne": name}},
            )
            for sim_doc, sim_meta, dist in zip(
                similar["documents"][0], similar["metadatas"][0], similar["distances"][0]
            ):
                if dist < CONNECTION_THRESHOLD:
                    connections["connections"].append({
                        "type":            "confirms" if dist < 0.10 else "related",
                        "distance":        round(dist, 4),
                        "fact_new":        ctext,
                        "source_new":      name,
                        "fact_existing":   sim_doc,
                        "source_existing": sim_meta.get("source", "?"),
                    })

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

    save_json(CONNECTIONS_FILE, connections)
    new_conns = [c for c in connections["connections"] if c["source_new"] == name]
    if new_conns:
        confirms = sum(1 for c in new_conns if c["type"] == "confirms")
        related  = sum(1 for c in new_conns if c["type"] == "related")
        print(f"  🔗 Cross-source connections: {confirms} confirming, {related} related")

    # Assemble topic notes deterministically (only topics that produced claims)
    final_topics = sorted(used_topics)
    print(f"  → Assembling {len(final_topics)} topic note(s)...")
    written = [t for t in final_topics if update_topic_file(t, facts_col)]

    sources[name] = {
        "processed_at":  datetime.now().isoformat(),
        "video_summary": outline["video_summary"],
        "section_count": len(sections),
        "claim_count":   claim_count,
        "entity_count":  entity_count,
        "topics":        written,
        "sections":      [
            {"title": (s.get("title") or f"Section {i+1}").strip(),
             "premise": (s.get("premise") or "").strip(),
             "conclusion": (s.get("conclusion") or "").strip()}
            for i, s in enumerate(sections)
        ],
    }
    save_json(SOURCES_FILE, sources)
    rebuild_index()

    print(f"  ✅ {name} complete")


def process_all(force=False):
    if not TRANSCRIPTS_DIR.exists():
        print("❌ transcripts/ directory not found. Run 'git pull' first.")
        sys.exit(1)

    sources   = load_json(SOURCES_FILE, {})
    all_txts  = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    pending   = all_txts if force else [t for t in all_txts if t.name not in sources]

    if not pending:
        print("✅ All transcripts already processed.")
        return

    print(f"Found {len(pending)} unprocessed transcript(s).")
    for t in pending:
        process_transcript(t, force=force)


def do_rebuild():
    """Wipe ChromaDB, topic notes, connections, and sources, then reprocess all."""
    print("🗑  Clearing ChromaDB index...")
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)

    if TOPICS_DIR.exists():
        for f in TOPICS_DIR.glob("*.md"):
            f.unlink()
        print("   cleared knowledge/topics/")

    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_json(CONNECTIONS_FILE, {"connections": []})

    if SOURCES_FILE.exists():
        bak = SOURCES_FILE.with_suffix(".json.bak")
        SOURCES_FILE.rename(bak)
        print(f"   sources.json backed up to {bak.name}")

    process_all(force=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--rebuild-index":
        rebuild_index()
        print(f"✅ Index rebuilt — {len(load_json(SOURCES_FILE, {}))} sources.")
        return

    check_ollama()

    if arg == "--rebuild":
        do_rebuild()
    elif arg == "--all":
        process_all()
    else:
        p = Path(arg)
        if not p.exists():
            alt = TRANSCRIPTS_DIR / p.name
            if alt.exists():
                p = alt
            else:
                print(f"❌ File not found: {arg}")
                sys.exit(1)
        process_transcript(p)


if __name__ == "__main__":
    main()
