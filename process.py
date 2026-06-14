#!/usr/bin/env python3
"""
Scribe knowledge pipeline (chained, whole-document).

The pipeline is decomposed into many short single-objective LLM calls because the
local model (qwen3:1.7b) is tiny and does far better with one job per call plus an
explicit verify-against-source step than with one big extraction prompt.

Pass A — SEGMENT (2 chained calls):
  A1 topics   : whole transcript -> 10-16 canonical topic labels (deduped).
  A2 sections : whole transcript + topics -> video_summary + 8-14 sections
                (each premise + conclusion); retries once if it under-segments.

Pass B — EXTRACT (2 chained calls per section):
  B1 draft    : section -> up to 8 candidate claims + entities + triples (few-shot).
  B2 verify   : section + drafts -> apply a 5-point rubric, drop/rewrite to grounded,
                specific, standalone CONCLUSIONS (self-refine against the transcript).

Pass C — STRUCTURE (1 call per topic, inside note assembly):
  C1 structure: topic claims -> pick up to N section types from a catalog
                (Key Takeaway / Key Numbers / How-To / Evidence / Why It Works / ...)
                and fill them ONLY from the claims. Bullets that introduce a number
                absent from every claim are dropped (anti-hallucination guard).
The few-shot bank and rubric live in evals/claim_evals.md; design in plan_pipeline.md.

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

# Sectioning targets (Pass A) — raised: 5 topics was too few for a ~25-min video.
MIN_SECTIONS = 8
MAX_SECTIONS = 14
MAX_TOPICS   = 16
MIN_TOPICS   = 1
TARGET_TOPICS_LOW  = 10
TARGET_TOPICS_HIGH = 16

# Cosine distance below which two claims are flagged as related/confirming
CONNECTION_THRESHOLD = 0.20

# Ollama option sets (num_ctx is set explicitly so input is never silently truncated).
# The pipeline is now CHAINED: many short single-objective calls beat one big call for a
# 1.7B model (see plan_pipeline.md). Each step gets its own tuned options.
_BASE_SAMPLING = {"top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05}
TOPICS_OPTS   = {"num_ctx": 16384, "num_predict": 1024, "temperature": 0.2, **_BASE_SAMPLING}
SECTIONS_OPTS = {"num_ctx": 16384, "num_predict": 2048, "temperature": 0.2, **_BASE_SAMPLING}
DRAFT_OPTS    = {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.2, **_BASE_SAMPLING}
VERIFY_OPTS   = {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.1, **_BASE_SAMPLING}
STRUCTURE_OPTS= {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.3, **_BASE_SAMPLING}


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
# LLM prompts — thinking disabled via /no_think (kept compact for json mode).
#
# The pipeline is CHAINED into many short single-objective calls. qwen3:1.7b cannot
# find + ground + decontextualize + tag claims in one shot, so we decompose:
#   Pass A : A1 topics  -> A2 sections
#   Pass B : B1 draft   -> B2 verify/refine (self-refine against the transcript)
#   Pass C : C1 structure (pick 3-5 section types, fill them from claims only)
# Few-shot GOOD/BAD pairs come from evals/claim_evals.md.
# ---------------------------------------------------------------------------

# A shared, heavily-formatted preamble used by every system prompt. Repeats the single
# most important rule for a tiny model: NEVER invent; NEVER invert the source.
_GROUNDING_LAWS = (
    "==================== ABSOLUTE LAWS (read first, obey always) ====================\n"
    "1. Output ONLY valid JSON. No prose, no markdown, no code fences, no commentary.\n"
    "2. GROUNDING: every word you emit must be supported by the text you were given.\n"
    "   You may COPY and lightly rephrase the speaker. You may NOT add outside knowledge,\n"
    "   definitions, or generalities the speaker never stated.\n"
    "3. NEVER INVERT THE SOURCE. If unsure what the speaker meant, leave it out. Stating\n"
    "   the opposite of what was said is the worst possible error.\n"
    "4. When you have nothing valid to emit for a field, emit an empty array. Empty is\n"
    "   always better than invented.\n"
    "================================================================================\n"
)

# Compressed few-shot bank (from evals/claim_evals.md) shown to the draft + verify steps.
_FEWSHOT = (
    "----- WHAT A GOOD CLAIM LOOKS LIKE (imitate these) -----\n"
    'GOOD: "A franchise owner named Rob referred over 26 Alloy gym owners and added '
    'more than $30,000 per month to the agency, because franchise owners refer each '
    'other freely." (grounded names+numbers, lands a real takeaway, extra context is '
    "from the transcript)\n"
    'GOOD: "AI follows up with leads within five minutes instead of the typical 42 '
    'hours, increasing the chance of converting that lead by 400%." (short is fine when '
    "the numbers carry the point)\n"
    'GOOD: "A gym owner with four locations got 32 memberships and 12 PT agreements in '
    '8 days (a 92% conversion rate) by reactivating her existing database with zero ad '
    'spend." (the bare 92% is meaningless; transcript context makes it land)\n"'
    "----- WHAT A BAD CLAIM LOOKS LIKE (NEVER produce these) -----\n"
    'BAD: "AI reaches out to past clients with personalized offers, driving high '
    'response rates and revenue." (vague verbs, no number, no mechanism)\n'
    'BAD: "The five AI workers can be replaced by cheaper freelancers." (INVERTS the '
    "source — the speaker says the opposite; a single automation is what gets replaced)\n"
    'BAD: "The Outreach Specialist is called a database reactivation." (a label, not a '
    "takeaway)\n"
    'BAD: "Christina\'s review highlights the effectiveness of AI." (meta-narration about '
    "the video, no concrete payload)\n"
)

# ---- Pass A1: topics only -----------------------------------------------------------
_TOPICS_SYSTEM = (
    "/no_think\n"
    "You are a precise knowledge-graph topic designer. You read an ENTIRE video "
    "transcript and produce the canonical set of topic labels that organize its ideas.\n"
    + _GROUNDING_LAWS
)

_TOPICS_TMPL = """Below is the FULL transcript of a video, wrapped in markers.

<<<TRANSCRIPT_START>>>
{transcript}
<<<TRANSCRIPT_END>>>

TASK: choose the canonical TOPIC LABELS for this video — the distinct ideas a reader
would want as nodes in a knowledge graph.

Think about the distinct things the speaker actually discusses: each named method or
tool, each distinct strategy, each distinct problem, the supporting proof/examples.
This video is dense — aim for {lo} to {hi} topics. Too few topics buries ideas together.

RULES:
- {lo}-{hi} labels, each 1-3 words, Title Case.
- Each label names something the speaker REALLY discusses (grounded).
- DEDUPLICATE: merge synonyms into ONE label (never list both "Lead Generation" and
  "Getting Leads"). No near-synonyms, no overlapping pairs.
- Order from most to least central.

Return EXACTLY this JSON and nothing else:
{{"topics": ["Topic A", "Topic B", "..."]}}
"""

# ---- Pass A2: sections (given topics) -----------------------------------------------
_SECTIONS_SYSTEM = (
    "/no_think\n"
    "You are a precise transcript analyst. You split an ENTIRE transcript into the "
    "natural sections in which the speaker actually moves through ideas.\n"
    + _GROUNDING_LAWS
)

_SECTIONS_TMPL = """Below is the FULL transcript of a video, wrapped in markers.

<<<TRANSCRIPT_START>>>
{transcript}
<<<TRANSCRIPT_END>>>

The canonical topics already chosen for this video are: {topic_list}

TASK: split the video into {min_s} to {max_s} natural sections IN ORDER, and write a
one-sentence summary of the whole video.

Return EXACTLY this JSON and nothing else:
{{
  "video_summary": "one sentence: the specific thing this video teaches or argues",
  "sections": [
    {{
      "title": "short section title",
      "start_marker": "the first 8 words of this section, copied VERBATIM from the transcript",
      "premise": "the specific setup the speaker makes to OPEN this section (one sentence, from the transcript)",
      "conclusion": "the specific takeaway the speaker lands on to CLOSE this section (one sentence, from the transcript)"
    }}
  ]
}}

RULES:
- {min_s}-{max_s} sections.
- start_marker MUST be copied verbatim from the transcript (it is used to locate the
  section). Use the actual first words where that idea begins.
- premise and conclusion quote concrete numbers, names, and steps. Never generic.
"""

# ---- Pass B1: draft claims ----------------------------------------------------------
_DRAFT_SYSTEM = (
    "/no_think\n"
    "You extract specific, factual CONCLUSIONS from one transcript section. A good claim "
    "is a useful takeaway with real context (numbers, names, mechanisms), NOT a vague "
    "platitude and NOT a bare label.\n"
    + _GROUNDING_LAWS
    + _FEWSHOT
)

_DRAFT_TMPL = """This is ONE section of a video, titled "{title}".
The video's canonical topics are: {topic_list}

<<<SECTION_START>>>
{section}
<<<SECTION_END>>>

TASK: draft the specific knowledge stated in THIS section. Over-generate slightly; a
later step will verify and trim. For each claim, prefer a useful CONCLUSION with the
surrounding context the speaker gives (numbers, named people/companies, the mechanism)
— but pull that context ONLY from the section above.

Return EXACTLY this JSON and nothing else:
{{
  "claims": [
    {{"claim": "a specific, standalone takeaway with its transcript context",
      "topic": "the single best-fitting label from the canonical topics above"}}
  ],
  "entities": [
    {{"name": "Name", "type": "person|org|product|tool|method|metric",
      "mention": "the exact phrase the transcript uses"}}
  ],
  "triples": [{{"subject": "X", "predicate": "verb phrase", "object": "Y"}}]
}}

RULES:
- up to 8 candidate claims. Each is ONE fact, understandable alone (no dangling "this"/
  "the example"). Quote the specifics the speaker gave.
- NO definitions of common terms. NO meta-narration about the video or its reviews.
- topic MUST be exactly one of: {topic_list}.
- entities: only things explicitly named here, max 6.
- triples: from the claims, max 6; subject/object short, predicate a short verb phrase.
"""

# ---- Pass B2: verify + refine (self-refine) -----------------------------------------
_VERIFY_SYSTEM = (
    "/no_think\n"
    "You are a strict fact-checker and editor. You receive draft claims and the original "
    "transcript section. You KEEP only claims that pass the rubric, REWRITE salvageable "
    "ones to be grounded and specific, and DROP the rest. You never invent.\n"
    + _GROUNDING_LAWS
    + _FEWSHOT
)

_VERIFY_TMPL = """This is ONE section of a video, titled "{title}".
The canonical topics are: {topic_list}

<<<SECTION_START>>>
{section}
<<<SECTION_END>>>

DRAFT CLAIMS to check (JSON):
{draft_claims}

TASK: apply this 5-point rubric to EACH draft claim:
  (1) GROUNDED — every number/name/fact is in the section above (and not inverted).
  (2) SPECIFIC — has a concrete payload (number, named entity, named mechanism/outcome),
      not a vague gesture like "drives revenue" or "is effective".
  (3) STANDALONE — understandable alone; no dangling "this"/"it"/"the example".
  (4) CONCLUSION-BEARING — lands a real takeaway, not a bare label/definition.
  (5) USEFUL CONTEXT — carries enough transcript context to make the point land (short
      is fine when a number speaks for itself).

For each draft claim:
- if it scores 4-5, KEEP it (you may tighten wording, staying grounded);
- if it is salvageable (a real fact buried under vagueness), REWRITE it to pass, adding
  context ONLY from the section above;
- otherwise DROP it.
You may also ADD a strong claim the draft missed, if it is clearly stated in the section.

Return EXACTLY this JSON and nothing else:
{{"claims": [{{"claim": "the kept or rewritten claim", "topic": "one of the canonical topics"}}]}}

RULES:
- max 6 claims. Fewer, excellent claims beat many weak ones.
- topic MUST be exactly one of: {topic_list}.
- NEVER add a fact not present in the section. Empty array is allowed.
"""

# ---- Pass C1: structured conclusions per topic --------------------------------------
SECTION_TYPE_CATALOG = [
    ("Key Takeaway",          "the single most important conclusion — include almost always"),
    ("Key Numbers",           "the concrete metrics/stats — include if 2+ numbers exist"),
    ("How-To / Method",       "ordered steps the speaker gives — include if a procedure is described"),
    ("Evidence & Examples",   "named cases/testimonials backing a claim — include if a named example exists"),
    ("Why It Works",          "the stated mechanism/causal reason — include if a 'because' is explained"),
    ("Implications",          "what follows for the practitioner — include if claims imply an action"),
    ("Actionable Advice",     "explicit do-this directives — include if instructions are given"),
    ("Contradictions / Tensions", "claims in tension or a 'most people do X but...' reversal — include if present"),
    ("Caveats & Limits",      "stated conditions/exceptions/what won't work — include if the speaker hedges"),
    ("Open Questions",        "what the transcript leaves unanswered — include sparingly"),
    ("Notable Quotes",        "a verbatim memorable line — include if a quotable line exists"),
]

_STRUCTURE_SYSTEM = (
    "/no_think\n"
    "You are a knowledge-note editor. Given the claims gathered for ONE topic, you select "
    "the 3-5 most applicable SECTION TYPES from a fixed catalog and fill each ONLY from "
    "those claims. You add structure and synthesis; you never add new facts.\n"
    + _GROUNDING_LAWS
)

_STRUCTURE_TMPL = """TOPIC: "{topic}"

CLAIMS gathered for this topic (each is grounded in the transcript):
{claims_block}

SECTION TYPE CATALOG (name — when to include):
{catalog}

TASK:
1. SELECT UP TO {max_sec} section types from the catalog that these claims actually
   support. It is BETTER to return fewer well-grounded sections than to pad.
   Skip any section you would have to INVENT content to fill.
2. FILL each selected section with 1-4 short bullet points, drawn ONLY from the claims
   above (you may rephrase/synthesize, never add new facts or numbers).
3. Write a one-line HEADLINE: the single best takeaway for this topic.

Return EXACTLY this JSON and nothing else:
{{
  "headline": "one-line best takeaway for this topic",
  "sections": [
    {{"type": "exact section type name from the catalog", "bullets": ["point", "point"]}}
  ]
}}

RULES:
- Up to {max_sec} sections (you have {n_claims} claim(s) to work with — do NOT pad beyond
  what they support). "type" MUST match a catalog name exactly.
- bullets are short, specific, and contain only facts present in the claims above.
- CRITICAL: every number, percentage, dollar amount, name, and company in a bullet MUST
  appear in the claims above. Do NOT introduce ANY number or example that is not there.
  Inventing a statistic or a case study is the worst possible error.
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


def _dedupe_topics(topics):
    seen, uniq = set(), []
    for t in topics:
        if isinstance(t, str) and t.strip() and t.strip().lower() not in seen:
            seen.add(t.strip().lower())
            uniq.append(t.strip())
    return uniq


def llm_segment(full_text):
    """Pass A — CHAINED: A1 topics, then A2 sections. → {video_summary, topics, sections}."""
    # A1 — topics only
    a1 = _chat_json(
        _TOPICS_SYSTEM,
        _TOPICS_TMPL.format(transcript=full_text,
                            lo=TARGET_TOPICS_LOW, hi=TARGET_TOPICS_HIGH),
        TOPICS_OPTS,
    ) or {}
    topics = _dedupe_topics(a1.get("topics", []))[:MAX_TOPICS]

    # A2 — sections, conditioned on the chosen topics. The 1.7B model under-segments a
    # dense video unpredictably; retry once if it returns fewer than MIN_SECTIONS, since
    # finer sections give more topics a chance to collect claims in Pass B.
    best_sections, best_summary = [], ""
    for _ in range(2):
        a2 = _chat_json(
            _SECTIONS_SYSTEM,
            _SECTIONS_TMPL.format(transcript=full_text,
                                  topic_list=", ".join(topics) if topics else "(none)",
                                  min_s=MIN_SECTIONS, max_s=MAX_SECTIONS),
            SECTIONS_OPTS,
        ) or {}
        secs = [s for s in a2.get("sections", []) if isinstance(s, dict)][:MAX_SECTIONS]
        if len(secs) > len(best_sections):
            best_sections = secs
            best_summary = (a2.get("video_summary") or "").strip() or best_summary
        if len(best_sections) >= MIN_SECTIONS:
            break

    return {
        "video_summary": best_summary,
        "topics": topics,
        "sections": best_sections,
    }


def _clean_triples(raw_triples):
    JUNK_PRED = {"verb phrase", "predicate", "", "is", "are", "was", "were"}
    triples = []
    for t in raw_triples or []:
        if not isinstance(t, dict):
            continue
        s = str(t.get("subject", "")).strip()
        p = str(t.get("predicate", "")).strip()
        o = str(t.get("object", "")).strip()
        if not (s and p and o) or p.lower() in JUNK_PRED:
            continue
        if len(s.split()) > 6 or len(o.split()) > 6:
            continue
        triples.append({"subject": s, "predicate": p, "object": o})
    return triples[:6]


def _snap_claims(raw_claims, topic_list, limit):
    allowed = {t.lower(): t for t in topic_list}
    out = []
    for c in (raw_claims or [])[:limit]:
        if not isinstance(c, dict):
            continue
        text = (c.get("claim") or "").strip()
        if not text:
            continue
        topic = allowed.get((c.get("topic") or "").strip().lower())
        if not topic:
            topic = topic_list[0] if topic_list else "General"
        out.append({"claim": text, "topic": topic})
    return out


def llm_extract(section_text, section_title, topic_list):
    """Pass B — CHAINED: B1 draft (over-generate) → B2 verify/refine against the section.

    Returns {claims:[{claim,topic}], entities, triples}.
    """
    tl = ", ".join(topic_list) if topic_list else "(none)"

    # B1 — draft candidates (up to 8)
    draft = _chat_json(
        _DRAFT_SYSTEM,
        _DRAFT_TMPL.format(title=section_title, topic_list=tl, section=section_text),
        DRAFT_OPTS,
    ) or {}
    draft_claims = _snap_claims(draft.get("claims", []), topic_list, limit=8)
    entities = [e for e in draft.get("entities", []) if isinstance(e, dict)][:6]
    triples  = _clean_triples(draft.get("triples", []))

    # B2 — verify + refine against the transcript section (self-refine step)
    claims = draft_claims
    if draft_claims:
        verified = _chat_json(
            _VERIFY_SYSTEM,
            _VERIFY_TMPL.format(
                title=section_title, topic_list=tl, section=section_text,
                draft_claims=json.dumps([{"claim": c["claim"], "topic": c["topic"]}
                                         for c in draft_claims], ensure_ascii=False),
            ),
            VERIFY_OPTS,
        )
        # Trust the verifier only if it returned a usable list; else fall back to draft.
        if verified and isinstance(verified.get("claims"), list):
            refined = _snap_claims(verified["claims"], topic_list, limit=6)
            if refined:
                claims = refined

    return {"claims": claims[:6], "entities": entities, "triples": triples}


_NUM_RE = re.compile(r"\d[\d,\.]*\s?%?")


def _numbers_in(text):
    """Normalized set of numeric tokens (digits + optional %) appearing in text."""
    return {re.sub(r"[,\s]", "", m.group()) for m in _NUM_RE.finditer(text)}


def llm_structure(topic, claim_texts):
    """Pass C — pick section types and fill them from this topic's claims only.

    The section ceiling scales with claim count so a thin topic isn't pressured to pad
    (and thereby invent). A number appearing in a bullet but in NONE of the claims is a
    hallucination; such bullets are dropped.

    Returns {"headline": str, "sections": [{"type", "bullets":[...]}, ...]} or None.
    """
    if not claim_texts:
        return None
    # 1 claim -> up to 2 sections; 2 -> 3; 3+ -> 5. Floor is "don't pad".
    max_sec = 2 if len(claim_texts) == 1 else (3 if len(claim_texts) == 2 else 5)
    catalog = "\n".join(f"- {name} — {when}" for name, when in SECTION_TYPE_CATALOG)
    claims_block = "\n".join(f"- {c}" for c in claim_texts)
    valid_types = {name for name, _ in SECTION_TYPE_CATALOG}
    allowed_nums = set().union(*(_numbers_in(c) for c in claim_texts)) if claim_texts else set()

    data = _chat_json(
        _STRUCTURE_SYSTEM,
        _STRUCTURE_TMPL.format(topic=topic, claims_block=claims_block, catalog=catalog,
                               max_sec=max_sec, n_claims=len(claim_texts)),
        STRUCTURE_OPTS,
    )
    if not data:
        return None

    sections = []
    for s in data.get("sections", []):
        if not isinstance(s, dict):
            continue
        stype = (s.get("type") or "").strip()
        if stype not in valid_types:
            continue
        bullets = []
        for b in (s.get("bullets") or []):
            bt = str(b).strip()
            if not bt:
                continue
            # Drop any bullet that introduces a number absent from every claim — that is
            # an invented statistic (the failure mode the rubric exists to prevent).
            if _numbers_in(bt) - allowed_nums:
                continue
            bullets.append(bt)
        if bullets:
            sections.append({"type": stype, "bullets": bullets[:4]})
    sections = sections[:max_sec]

    return {"headline": (data.get("headline") or "").strip(), "sections": sections}


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
    claim_texts = [d for d, _ in claims]

    # Pass C — structured conclusions: model picks 3-5 section types and fills them from
    # THIS topic's claims only. Degrades gracefully to claims+relationships on failure.
    structure = llm_structure(topic, claim_texts)

    # Headline: prefer the model's synthesized one; else shortest substantive claim.
    eligible = [d for d, _ in claims if len(d.split()) >= 8]
    fallback_headline = min(eligible, key=lambda d: len(d.split())) if eligible else docs[0]
    headline = (structure or {}).get("headline") or fallback_headline

    lines = [f"# {topic}", "", f"> {headline}", ""]

    # Structured conclusion sections (above Claims)
    if structure and structure.get("sections"):
        for sec in structure["sections"]:
            lines.append(f"## {sec['type']}")
            for b in sec["bullets"]:
                lines.append(f"- {b}")
            lines.append("")

    lines.append("## Claims")
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
