#!/usr/bin/env python3
"""
Scribe knowledge pipeline (chained, windowed, with LLM connection analysis).

The pipeline is decomposed into many short single-objective LLM calls because the
local model (qwen3:1.7b) is tiny and does far better with one job per call plus an
explicit verify-against-source step than with one big extraction prompt. Each pass
now opens with a short single-objective PLAN/THINK call that scaffolds the producing
call (the model plans how it will write before it writes).

Pass A — SEGMENT (WINDOWED / hierarchical map-reduce):
  A 40-50k-word (~3-5 h) transcript is ~55-65k tokens and does NOT fit in qwen3:1.7b's
  40960-token window, so Pass A is windowed:
    * split the transcript into ~6k-word windows with small overlap (WINDOW_WORDS /
      WINDOW_OVERLAP_WORDS);
    * per window: A0 plan (CoT outline) -> A1 topics -> A2 sections;
    * REDUCE: merge + dedupe per-window topics into a global canonical topic set
      (A3, LLM-assisted) and concatenate the ordered per-window sections.
  Topic/section counts scale with transcript length, so a 4-hour course gets many
  more nodes than a 6-minute clip.

Pass B — EXTRACT (3 chained calls per section):
  B0 plan     : section -> a short list of the concrete facts worth extracting (CoT).
  B1 draft    : section + plan -> up to 8 candidate claims + entities + triples.
  B2 verify   : section + drafts -> apply a 5-point rubric, drop/rewrite to grounded,
                specific, standalone CONCLUSIONS (self-refine against the transcript).

Pass C — STRUCTURE (2 calls per topic, inside note assembly):
  C0 plan     : topic claims -> which catalog section types the claims support (CoT).
  C1 structure: topic claims + plan -> fill the chosen section types ONLY from the
                claims. Bullets that introduce a number absent from every claim are
                dropped (anti-hallucination guard).

Pass D — CONNECT (LLM-based cross-node relationship analysis, the graph's substance):
  After all topics/claims/embeddings exist, cluster topics by embedding similarity into
  "suites" of related nodes (especially ACROSS the three sources). Per suite:
    D0 plan   : list the genuine points of agreement / building-on / contradiction (CoT);
    D1 write  : turn the plan into plain natural-language CONNECTION SENTENCES (verb
                inline, NOT arrow-triples, NOT hyper-specific fact dumps).
  Results are stored in knowledge/connections.json keyed by topic, rendered into each
  note's `## Connections` section, AND drive the topic<->topic graph edges.

Storage:
  chunks — transcript SECTIONS (rich context for RAG retrieval)
  facts  — atomic CLAIMS (precise semantic search; one canonical topic each)
Topic notes (knowledge/topics/*.md) are assembled DETERMINISTICALLY from the stored
claims + the Pass-D connection sentences — no free-text synthesis of the claims
themselves, so nothing is re-explained or truncated.

The few-shot bank and rubric live in evals/claim_evals.md; design in plan_pipeline.md.

Usage:
    python3 process.py <transcript.txt>    # process a single file
    python3 process.py --all               # process all unprocessed transcripts
    python3 process.py --rebuild           # clear ChromaDB and reprocess everything
    python3 process.py --connect           # (re)run Pass D connection analysis only
    python3 process.py --rebuild-index     # regenerate knowledge/_index.md only
"""

import os
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

# --- Gemini (preferred LLM backend, free tier; qwen is the local fallback) ----------
# All non-embedding LLM calls flow through llm_json(), which prefers Gemini when a key is
# present and Gemini isn't in a rate-limit cooldown, and falls back to local qwen otherwise.
# Embeddings stay LOCAL (see embed()) — the stored vector space + server.py retrieval
# depend on nomic-embed-text, and embeddings are the highest-volume calls.
GEMINI_API_KEY_ENV    = "GEMINI_API_KEY"
GEMINI_MODEL          = "gemini-2.5-flash"   # primary
GEMINI_MODEL_FALLBACK = "gemini-2.0-flash"   # if the primary is unavailable
# If a 429 wants us idle longer than this, fall straight back to qwen instead of sleeping.
GEMINI_MAX_SLEEP_S    = 15
GEMINI_COOLDOWN_CAP_S = 3600  # never cool down longer than an hour before re-probing

# --- Windowing (Pass A, task 0) ----------------------------------------------------
# A 40-50k-word transcript is ~55-65k tokens and overflows qwen3:1.7b's 40960-token
# window, so Pass A runs over context-safe word windows with small overlap. ~6k words
# (~8k tokens) sits comfortably inside the 16384-token segment context with room for the
# prompt scaffolding. Overlap keeps an idea that straddles a boundary from being lost.
WINDOW_WORDS         = 6000
WINDOW_OVERLAP_WORDS = 400

# Per-window sectioning/topic targets. The GLOBAL counts scale with length because a long
# transcript simply has more windows (a 6k-word clip = 1 window; a 50k-word video = ~9).
WIN_MIN_SECTIONS = 3
WIN_MAX_SECTIONS = 6
WIN_TOPICS_LOW   = 4
WIN_TOPICS_HIGH  = 8

# Global caps after the REDUCE step — generous so length can scale, but bounded so the
# graph stays legible. A near-synonym merge keeps the set meaningful.
MAX_TOPICS_GLOBAL   = 60
MAX_SECTIONS_GLOBAL = 80
MIN_TOPICS = 1

# Cosine distance below which two claims are flagged as related/confirming (legacy
# claim-level cosine log; Pass D now produces the meaningful node-level relationships).
CONNECTION_THRESHOLD = 0.20

# --- Pass D (LLM connection analysis, task 2) --------------------------------------
# Topics whose embedding cosine distance is below this are candidates for the same
# "suite" fed to the LLM to reason about (kept loose; the LLM is the real filter).
SUITE_DISTANCE      = 0.45
SUITE_MAX_SIZE      = 6     # cap nodes per suite so the prompt stays in-context
SUITE_MIN_SIZE      = 2     # a suite needs >=2 nodes to have a relationship

# Ollama option sets (num_ctx is set explicitly so input is never silently truncated).
# The pipeline is CHAINED: many short single-objective calls beat one big call for a
# 1.7B model (see plan_pipeline.md). Each step gets its own tuned options. Each pass now
# also has a small *_PLAN_OPTS step that scaffolds the producing call (task 1).
_BASE_SAMPLING = {"top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05}
PLAN_OPTS      = {"num_ctx": 16384, "num_predict": 768,  "temperature": 0.3, **_BASE_SAMPLING}
TOPICS_OPTS    = {"num_ctx": 16384, "num_predict": 1024, "temperature": 0.2, **_BASE_SAMPLING}
SECTIONS_OPTS  = {"num_ctx": 16384, "num_predict": 2048, "temperature": 0.2, **_BASE_SAMPLING}
REDUCE_OPTS    = {"num_ctx": 16384, "num_predict": 1536, "temperature": 0.1, **_BASE_SAMPLING}
DRAFT_PLAN_OPTS= {"num_ctx": 8192,  "num_predict": 768,  "temperature": 0.3, **_BASE_SAMPLING}
DRAFT_OPTS     = {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.2, **_BASE_SAMPLING}
VERIFY_OPTS    = {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.1, **_BASE_SAMPLING}
STRUCT_PLAN_OPTS={"num_ctx": 8192,  "num_predict": 512,  "temperature": 0.3, **_BASE_SAMPLING}
STRUCTURE_OPTS = {"num_ctx": 8192,  "num_predict": 1536, "temperature": 0.3, **_BASE_SAMPLING}
CONNECT_PLAN_OPTS={"num_ctx":12288, "num_predict": 1024, "temperature": 0.3, **_BASE_SAMPLING}
CONNECT_OPTS   = {"num_ctx": 12288, "num_predict": 1024, "temperature": 0.3, **_BASE_SAMPLING}


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

# ---- Pass A0: per-window PLAN (CoT scaffold, task 1) --------------------------------
_AWIN_PLAN_SYSTEM = (
    "/no_think\n"
    "You are a transcript analyst sketching a quick outline of ONE excerpt of a longer "
    "video before formally segmenting it. Think out loud, briefly, in JSON.\n"
    + _GROUNDING_LAWS
)

_AWIN_PLAN_TMPL = """Below is ONE EXCERPT (part {idx} of {total}) of a longer video.

<<<EXCERPT_START>>>
{transcript}
<<<EXCERPT_END>>>

TASK (planning only — do NOT write final labels yet): jot down the distinct things the
speaker covers in THIS excerpt, so the next step can name topics and split sections well.

Return EXACTLY this JSON and nothing else:
{{
  "covers": ["a short phrase for each distinct idea/method/tool/example actually discussed here"],
  "shifts": ["a short phrase marking each point where the speaker clearly moves to a new idea"]
}}

RULES:
- Each entry grounded in THIS excerpt only. 4-10 entries in "covers".
- No final formatting; this is a scratch outline for the next step.
"""

# ---- Pass A1: topics for ONE window -------------------------------------------------
_TOPICS_SYSTEM = (
    "/no_think\n"
    "You are a precise knowledge-graph topic designer. You read ONE excerpt of a video "
    "transcript and produce the canonical topic labels that organize its ideas.\n"
    + _GROUNDING_LAWS
)

_TOPICS_TMPL = """Below is ONE EXCERPT (part {idx} of {total}) of a longer video, wrapped in markers.

<<<EXCERPT_START>>>
{transcript}
<<<EXCERPT_END>>>

A scratch outline of what this excerpt covers (from the previous step):
{plan}

TASK: choose the canonical TOPIC LABELS for THIS EXCERPT — the distinct ideas a reader
would want as nodes in a knowledge graph.

Think about the distinct things the speaker actually discusses here: each named method or
tool, each distinct strategy, each distinct problem, the supporting proof/examples.
Aim for {lo} to {hi} topics for this excerpt. Too few buries ideas together.

RULES:
- {lo}-{hi} labels, each 1-3 words, Title Case.
- Each label names something the speaker REALLY discusses in this excerpt (grounded).
- DEDUPLICATE within this excerpt: merge synonyms into ONE label (never list both
  "Lead Generation" and "Getting Leads"). No near-synonyms, no overlapping pairs.
- Order from most to least central.

Return EXACTLY this JSON and nothing else:
{{"topics": ["Topic A", "Topic B", "..."]}}
"""

# ---- Pass A2: sections for ONE window (given that window's topics) ------------------
_SECTIONS_SYSTEM = (
    "/no_think\n"
    "You are a precise transcript analyst. You split ONE excerpt of a transcript into the "
    "natural sections in which the speaker actually moves through ideas.\n"
    + _GROUNDING_LAWS
)

_SECTIONS_TMPL = """Below is ONE EXCERPT (part {idx} of {total}) of a longer video, wrapped in markers.

<<<EXCERPT_START>>>
{transcript}
<<<EXCERPT_END>>>

The canonical topics chosen for THIS EXCERPT are: {topic_list}

TASK: split THIS EXCERPT into {min_s} to {max_s} natural sections IN ORDER, and write a
one-sentence summary of what this excerpt teaches or argues.

Return EXACTLY this JSON and nothing else:
{{
  "video_summary": "one sentence: the specific thing this excerpt teaches or argues",
  "sections": [
    {{
      "title": "short section title",
      "start_marker": "the first 8 words of this section, copied VERBATIM from the excerpt",
      "premise": "the specific setup the speaker makes to OPEN this section (one sentence, from the excerpt)",
      "conclusion": "the specific takeaway the speaker lands on to CLOSE this section (one sentence, from the excerpt)"
    }}
  ]
}}

RULES:
- {min_s}-{max_s} sections.
- start_marker MUST be copied verbatim from THIS EXCERPT (it is used to locate the
  section). Use the actual first words where that idea begins.
- premise and conclusion quote concrete numbers, names, and steps. Never generic.
"""

# ---- Pass A3: REDUCE — merge per-window topics into one canonical set ---------------
_REDUCE_SYSTEM = (
    "/no_think\n"
    "You are a knowledge-graph editor consolidating topic labels gathered from many "
    "excerpts of ONE long video into a single canonical, deduplicated set.\n"
    + _GROUNDING_LAWS
)

_REDUCE_TMPL = """These topic labels were collected from {n_win} consecutive excerpts of ONE long
video. Because the excerpts overlap and the same idea recurs, there are DUPLICATES and
NEAR-SYNONYMS that must be merged.

RAW TOPIC LABELS (with how many excerpts each appeared in):
{raw_block}

TASK: produce the CANONICAL topic set for the whole video.

RULES:
- MERGE synonyms and near-synonyms into ONE label (e.g. "Lead Generation" + "Getting
  Leads" + "Lead Gen" -> "Lead Generation"). Keep the clearest 1-3 word Title Case form.
- KEEP genuinely distinct ideas separate — a long video legitimately has many topics, so
  do NOT over-merge unrelated things. Aim to preserve the real breadth.
- Drop labels that are too vague to be a node on their own ("Tips", "Overview").
- Order from most to least central (frequency is a hint, not a rule).
- Return up to {max_t} labels.

Return EXACTLY this JSON and nothing else:
{{"topics": ["Canonical Topic A", "Canonical Topic B", "..."]}}
"""

# ---- Pass A (Gemini whole-transcript): segment the FULL transcript in ONE call ------
# Gemini's ~1M-token context holds an entire multi-hour transcript, so when Gemini is the
# backend we skip windowing entirely and ask for the canonical topics + ordered sections
# for the whole video at once (same output schema as the windowed map-reduce, minus the
# per-window bookkeeping). The qwen fallback keeps the windowed flow (its 40960-ctx can't
# hold a 50k-word transcript).
_WHOLE_SEGMENT_SYSTEM = (
    "You are a precise knowledge-graph designer and transcript analyst. You read an ENTIRE "
    "video transcript and produce (1) the canonical topic labels that organize its ideas "
    "and (2) the ordered natural sections the speaker moves through.\n"
    + _GROUNDING_LAWS
)

_WHOLE_SEGMENT_TMPL = """Below is the COMPLETE transcript of one video, wrapped in markers.

<<<TRANSCRIPT_START>>>
{transcript}
<<<TRANSCRIPT_END>>>

TASK: analyze the WHOLE transcript and return:
1. video_summary — one sentence: the specific thing this video teaches or argues.
2. topics — the canonical TOPIC LABELS (knowledge-graph nodes) for the whole video.
3. sections — the natural sections IN ORDER in which the speaker moves through ideas.

RULES for topics:
- {lo_t}-{hi_t} labels (scale with length: a long video has more), each 1-3 words, Title Case.
- Each label names something the speaker REALLY discusses (grounded).
- DEDUPLICATE: merge synonyms/near-synonyms into ONE label. Order most→least central.
- Drop labels too vague to be a node ("Tips", "Overview").

RULES for sections:
- {lo_s}-{hi_s} sections IN ORDER across the whole transcript.
- start_marker MUST be the first 8 words of the section copied VERBATIM from the transcript
  (it is used to locate the section in the raw text). Use the actual words where the idea begins.
- premise/conclusion quote concrete numbers, names, and steps. Never generic.

Return EXACTLY this JSON and nothing else:
{{
  "video_summary": "one sentence: the specific thing this video teaches or argues",
  "topics": ["Topic A", "Topic B", "..."],
  "sections": [
    {{
      "title": "short section title",
      "start_marker": "the first 8 words of this section, copied VERBATIM from the transcript",
      "premise": "the specific setup that OPENS this section (one sentence, from the transcript)",
      "conclusion": "the specific takeaway that CLOSES this section (one sentence, from the transcript)"
    }}
  ]
}}
"""

# ---- Pass B0: draft PLAN (CoT scaffold, task 1) ------------------------------------
_DRAFT_PLAN_SYSTEM = (
    "/no_think\n"
    "You are a transcript analyst noting which concrete facts in ONE section are worth "
    "extracting, BEFORE writing the final claims. Think briefly, in JSON.\n"
    + _GROUNDING_LAWS
)

_DRAFT_PLAN_TMPL = """This is ONE section of a video, titled "{title}".

<<<SECTION_START>>>
{section}
<<<SECTION_END>>>

TASK (planning only): list the concrete, extractable facts in this section — the
numbers, named people/companies/tools, the mechanisms, the outcomes — that a good claim
would be built around. Do NOT write the final claims yet.

Return EXACTLY this JSON and nothing else:
{{
  "facts": ["a short phrase naming each concrete fact worth a claim (with its number/name/mechanism)"],
  "skip":  ["a short phrase for anything here that is mere narration, filler, or a common-term definition to AVOID"]
}}

RULES:
- Every entry in "facts" must be grounded in THIS section. 3-8 entries.
- This is a scratch plan; the next step writes the actual claims from it.
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

The facts worth extracting here (from the previous planning step) are:
{plan}

TASK: draft the specific knowledge stated in THIS section, building on the planned facts
above. Over-generate slightly; a later step will verify and trim. For each claim, prefer
a useful CONCLUSION with the
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

# ---- Pass C0: structure PLAN (CoT scaffold, task 1) --------------------------------
_STRUCT_PLAN_SYSTEM = (
    "/no_think\n"
    "You decide which note SECTION TYPES a set of topic claims can support, BEFORE the "
    "note is filled in. You pick only what the claims genuinely back. Think briefly, JSON.\n"
    + _GROUNDING_LAWS
)

_STRUCT_PLAN_TMPL = """TOPIC: "{topic}"

CLAIMS gathered for this topic:
{claims_block}

SECTION TYPE CATALOG (name — when to include):
{catalog}

TASK (planning only): decide which section types these claims actually support. For each
one you would include, name it and say in a few words which claim(s) back it. Do NOT
write the bullets yet. Skip any section you would have to INVENT content to fill.

Return EXACTLY this JSON and nothing else:
{{"plan": [{{"type": "exact catalog name", "why": "which claim(s) support it"}}]}}

RULES:
- Choose at most {max_sec} section types. Fewer well-grounded beats padding.
- "type" MUST match a catalog name exactly.
"""

_STRUCTURE_SYSTEM = (
    "/no_think\n"
    "You are a knowledge-note editor. Given the claims gathered for ONE topic and a plan "
    "of which SECTION TYPES they support, you fill each chosen section ONLY from those "
    "claims. You add structure and synthesis; you never add new facts.\n"
    + _GROUNDING_LAWS
)

_STRUCTURE_TMPL = """TOPIC: "{topic}"

CLAIMS gathered for this topic (each is grounded in the transcript):
{claims_block}

The section types you decided these claims support (from the planning step):
{plan}

SECTION TYPE CATALOG (name — when to include):
{catalog}

TASK:
1. Use the planned section types (correct yourself only if a planned one is clearly
   unsupported). Use AT MOST {max_sec} section types. Fewer well-grounded beats padding.
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

# ---- Pass D0: connection PLAN (CoT scaffold, task 1 + 2) ---------------------------
_CONNECT_PLAN_SYSTEM = (
    "/no_think\n"
    "You are an analyst comparing several related knowledge-graph topics — often drawn "
    "from DIFFERENT source videos — to find how their ideas genuinely relate. You reason "
    "step by step about agreement, building-on, and especially CONTRADICTION. Think in JSON.\n"
    + _GROUNDING_LAWS
)

_CONNECT_PLAN_TMPL = """Below are {n} related topics. Each shows its source video and its key claims.
Topics may come from the SAME or DIFFERENT source videos.

{suite_block}

TASK (planning only — reason, do NOT write final sentences yet): compare these topics
and identify the GENUINE, MEANINGFUL relationships between them — the kind an analyst
would find insightful, not trivial restatements. Look hardest for:
- AGREEMENT: two topics (especially from different videos) making the same core argument.
- BUILDS-ON: one topic extends, enables, or is a prerequisite for another.
- CONTRADICTION / TENSION: topics that disagree or recommend opposing things.

Return EXACTLY this JSON and nothing else:
{{
  "relationships": [
    {{
      "topics": ["Topic A", "Topic B"],
      "kind": "agreement|builds-on|contradiction",
      "cross_source": true,
      "note": "one short phrase: what specifically connects them"
    }}
  ]
}}

RULES:
- "topics" must be EXACTLY two of the topic names shown above.
- Only include a relationship that is real and substantive. It is fine to return an empty
  list if these topics are merely adjacent and share nothing insightful.
- Prefer cross-source relationships; they are the most valuable.
- Grounded: base every relationship on the claims shown, never outside knowledge.
"""

# ---- Pass D1: connection WRITE — natural-language sentences (task 2) ----------------
_CONNECT_WRITE_SYSTEM = (
    "/no_think\n"
    "You turn an analyst's notes about how topics relate into clear, natural-language "
    "CONNECTION SENTENCES that a chatbot could reason from. Each sentence is plain English "
    "with the relationship verb INLINE — never an arrow-triple, never a hyper-specific "
    "fact dump.\n"
    + _GROUNDING_LAWS
)

_CONNECT_WRITE_TMPL = """Below are {n} related topics with their claims, and an analyst's notes on how they relate.

{suite_block}

ANALYST'S RELATIONSHIP NOTES (JSON):
{plan}

TASK: write each relationship as ONE plain-English CONNECTION SENTENCE that captures the
insight, so a reader (or chatbot) can extrapolate from it.

STYLE — imitate these (the verb is INLINE, the insight is general enough to be useful):
GOOD: "Both the lazy-system video and the sales-call video argue that fast AI follow-up is the single biggest conversion lever."
GOOD: "This builds on the niche-selection idea: picking a high-ticket niche is what makes the database-reactivation play worth automating."
GOOD: "This contradicts the cold-outreach approach, which claims paid ads are unnecessary when referrals compound on their own."
NEVER produce junk like these:
BAD: "Ad set budget optimization -> dictates -> daily spend"  (arrow syntax, no insight)
BAD: "GBT -> generates -> 25 million dollars"  (hyper-specific fact dump, not a connection)

Return EXACTLY this JSON and nothing else:
{{
  "connections": [
    {{
      "topics": ["Topic A", "Topic B"],
      "kind": "agreement|builds-on|contradiction",
      "sentence": "one natural-language sentence, verb inline, capturing the connection"
    }}
  ]
}}

RULES:
- "topics" must be exactly two of the topic names shown above.
- sentence: ONE sentence, plain Markdown prose, no arrows, no triple syntax, no bare
  number dumps. State the relationship as an insight, not as a fact about one node.
- Only emit connections grounded in the claims above. Empty list is allowed.
"""


# ---------------------------------------------------------------------------
# Unified LLM layer — Gemini-first, qwen fallback (task 1)
#
# Every non-embedding LLM call in the pipeline goes through llm_json(). It prefers Gemini
# (free tier, much stronger, ~1M-token context so long transcripts need no windowing) and
# falls back to local Ollama qwen3:1.7b whenever:
#   * GEMINI_API_KEY is unset (qwen-only, original behaviour preserved), or
#   * Gemini is in a rate-limit cooldown (a prior 429 asked us to wait too long), or
#   * a Gemini call fails outright for this request.
# On a 429/RESOURCE_EXHAUSTED we parse the suggested retry delay: if <=15s we sleep+retry
# once, otherwise (or on unknown/daily-quota) we set a module-level cooldown and fall back
# to qwen immediately instead of sitting idle.
# ---------------------------------------------------------------------------

# Module-level Gemini state. time.time() is fine here (plain script, not a workflow).
_gemini_client_obj   = None        # cached genai.Client (None until first use / disabled)
_gemini_client_tried = False       # have we attempted to build the client yet?
_gemini_cooldown_until = 0.0       # epoch secs; while now() < this, all calls go to qwen
_llm_stats = {"gemini": 0, "qwen": 0, "qwen_fallback": 0}  # call accounting for the summary


def _gemini_available():
    """Build (once) and return a Gemini client, or None if unavailable/disabled.

    Returns None when the API key is absent or the SDK isn't installed. Cooldown is checked
    separately by the caller so we can log the cooldown→qwen transition distinctly.
    """
    global _gemini_client_obj, _gemini_client_tried
    if _gemini_client_tried:
        return _gemini_client_obj
    _gemini_client_tried = True
    api_key = os.environ.get(GEMINI_API_KEY_ENV)
    if not api_key:
        print("  ⚙️  GEMINI_API_KEY not set — using local qwen for all LLM calls.")
        return None
    try:
        from google import genai
        _gemini_client_obj = genai.Client(api_key=api_key)
        print(f"  ⚙️  Gemini enabled ({GEMINI_MODEL}, fallback {GEMINI_MODEL_FALLBACK}); "
              f"qwen is the rate-limit fallback.")
    except Exception as e:  # noqa: BLE001 — SDK missing or client build failed
        print(f"  ⚙️  Gemini unavailable ({e}) — using local qwen for all LLM calls.")
        _gemini_client_obj = None
    return _gemini_client_obj


def _gemini_thinking_config():
    """Return a ThinkingConfig disabling thinking, or None if the SDK lacks it."""
    try:
        from google.genai import types
        return types.ThinkingConfig(thinking_budget=0)
    except Exception:  # noqa: BLE001 — old SDK; omit and let the model think
        return None


def _parse_retry_delay(err):
    """Best-effort extract of the suggested retry delay (seconds) from a Gemini 429 error.

    Looks for the RetryInfo `retryDelay: "32s"` hint Gemini returns. None if not present.
    """
    msg = str(err)
    m = re.search(r"retry[\s_]*delay['\"]?\s*[:={]\s*['\"]?(\d+(?:\.\d+)?)\s*s", msg, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"['\"]?seconds['\"]?\s*[:=]\s*(\d+)", msg, re.I)
    if m:
        return float(m.group(1))
    return None


def _is_rate_limit(err):
    msg = str(err).lower()
    return ("429" in msg or "resource_exhausted" in msg or "resource exhausted" in msg
            or "rate limit" in msg or "quota" in msg or "too many requests" in msg)


def _gemini_json(client, system, user, *, max_tokens, temperature):
    """One Gemini generate_content call in JSON mode, parsed. Returns dict.

    Raises on failure (rate-limit or otherwise) so the caller can decide fallback/cooldown.
    """
    from google.genai import types
    cfg_kwargs = dict(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json",
    )
    tcfg = _gemini_thinking_config()
    if tcfg is not None:
        cfg_kwargs["thinking_config"] = tcfg
    config = types.GenerateContentConfig(**cfg_kwargs)

    last_err = None
    for model in (GEMINI_MODEL, GEMINI_MODEL_FALLBACK):
        try:
            resp = client.models.generate_content(
                model=model, contents=user, config=config)
            raw = (getattr(resp, "text", None) or "").strip()
            if not raw:
                raise ValueError("empty Gemini response")
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last_err = e
            # On a rate-limit, don't waste the fallback model (same quota bucket) — surface
            # it immediately so the caller can cool down / fall back to qwen.
            if _is_rate_limit(e):
                raise
            continue
    raise last_err if last_err else RuntimeError("Gemini call failed")


def llm_json(system, user, *, max_tokens, temperature=0.2, qwen_options=None):
    """Unified JSON LLM call: Gemini when available + not cooling down, else qwen.

    `qwen_options` is the Ollama options dict used for the fallback path (num_ctx etc.).
    Returns a parsed dict, or None if every backend failed.
    """
    global _gemini_cooldown_until
    qwen_options = qwen_options or DRAFT_OPTS
    client = _gemini_available()

    if client is not None:
        now = time.time()
        if now >= _gemini_cooldown_until:
            if _gemini_cooldown_until:           # cooldown just expired — re-probe Gemini
                print("  ⚙️  Gemini cooldown elapsed — re-probing Gemini." + " " * 20)
                _gemini_cooldown_until = 0.0
            try:
                out = _gemini_json(client, system, user,
                                   max_tokens=max_tokens, temperature=temperature)
                _llm_stats["gemini"] += 1
                return out
            except Exception as e:  # noqa: BLE001
                if _is_rate_limit(e):
                    delay = _parse_retry_delay(e)
                    if delay is not None and delay <= GEMINI_MAX_SLEEP_S:
                        # Short wait — sleep then retry Gemini once before giving up.
                        print(f"  ⚙️  Gemini rate-limited; retry in {delay:.0f}s "
                              f"(<= {GEMINI_MAX_SLEEP_S}s), waiting." + " " * 12)
                        time.sleep(delay + 0.5)
                        try:
                            out = _gemini_json(client, system, user,
                                               max_tokens=max_tokens, temperature=temperature)
                            _llm_stats["gemini"] += 1
                            return out
                        except Exception as e2:  # noqa: BLE001
                            e = e2  # fall through to cooldown + qwen
                    # Long / unknown / daily-quota delay → cool down + fall back to qwen.
                    cd = delay if (delay and delay > GEMINI_MAX_SLEEP_S) else GEMINI_COOLDOWN_CAP_S
                    cd = min(cd, GEMINI_COOLDOWN_CAP_S)
                    _gemini_cooldown_until = time.time() + cd
                    print(f"  ⚙️  Gemini rate-limited (retry ~{int(cd)}s) — cooling down, "
                          f"falling back to qwen." + " " * 8)
                else:
                    print(f"  ⚙️  Gemini call failed ({str(e)[:80]}) — falling back to qwen "
                          f"for this call." + " " * 8)
                _llm_stats["qwen_fallback"] += 1
                return _chat_json(system, user, qwen_options)
        else:
            # In cooldown — straight to qwen, no idle wait.
            _llm_stats["qwen_fallback"] += 1
            return _chat_json(system, user, qwen_options)

    # No Gemini at all — qwen-only path (original behaviour).
    _llm_stats["qwen"] += 1
    return _chat_json(system, user, qwen_options)


def _llm_summary():
    g = _llm_stats["gemini"]
    qf = _llm_stats["qwen_fallback"]
    q = _llm_stats["qwen"]
    parts = [f"{g} Gemini"]
    if qf:
        parts.append(f"{qf} qwen (fallback)")
    if q:
        parts.append(f"{q} qwen")
    print(f"\n  📊 LLM calls: {', '.join(parts)}  (total {g + qf + q})")


def _chat_json(system, user, options):
    """One Ollama chat call in JSON mode, thinking-stripped, parsed. None on failure.

    This is the qwen fallback implementation used by llm_json(); it preserves the original
    /no_think + <think>-stripping + retry behaviour.
    """
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


def _llm(system, user, opts):
    """Convenience wrapper: route a pass through llm_json, deriving Gemini's max_tokens /
    temperature from the qwen options dict (num_predict / temperature) so call sites read
    the same as before. Gemini gets a roomier token budget since it has no tiny context."""
    max_tokens = max(int(opts.get("num_predict", 1024)), 1024)
    temperature = float(opts.get("temperature", 0.2))
    return llm_json(system, user, max_tokens=max_tokens, temperature=temperature,
                    qwen_options=opts)


def _dedupe_topics(topics):
    seen, uniq = set(), []
    for t in topics:
        if isinstance(t, str) and t.strip() and t.strip().lower() not in seen:
            seen.add(t.strip().lower())
            uniq.append(t.strip())
    return uniq


def _windows(full_text):
    """Split text into ~WINDOW_WORDS-word windows with WINDOW_OVERLAP_WORDS overlap.

    Windowing is what lets a 40-50k-word (~60k-token) transcript be processed by a model
    with a 40960-token window without truncation. Overlap keeps an idea straddling a
    boundary from being dropped. A short transcript yields a single window (no overlap).
    """
    words = full_text.split()
    if len(words) <= WINDOW_WORDS:
        return [full_text]
    step = WINDOW_WORDS - WINDOW_OVERLAP_WORDS
    out = []
    i = 0
    while i < len(words):
        out.append(" ".join(words[i:i + WINDOW_WORDS]))
        if i + WINDOW_WORDS >= len(words):
            break
        i += step
    return out


def _fmt_plan(plan):
    """Render an A0/B0 plan dict into a compact bullet block for the next prompt."""
    if not isinstance(plan, dict):
        return "(no plan)"
    lines = []
    for key in ("covers", "shifts", "facts", "skip"):
        vals = [str(v).strip() for v in (plan.get(key) or []) if str(v).strip()]
        if vals:
            label = {"covers": "covers", "shifts": "topic shifts",
                     "facts": "extractable facts", "skip": "avoid"}[key]
            lines.append(f"{label}: " + "; ".join(vals[:10]))
    return "\n".join(lines) or "(no plan)"


def _segment_window(win_text, idx, total):
    """A0 plan -> A1 topics -> A2 sections for ONE window. Returns (topics, sections, summary)."""
    # A0 — plan (CoT scaffold)
    plan = _llm(
        _AWIN_PLAN_SYSTEM,
        _AWIN_PLAN_TMPL.format(transcript=win_text, idx=idx, total=total),
        PLAN_OPTS,
    ) or {}
    plan_block = _fmt_plan(plan)

    # A1 — topics for this window
    a1 = _llm(
        _TOPICS_SYSTEM,
        _TOPICS_TMPL.format(transcript=win_text, idx=idx, total=total, plan=plan_block,
                            lo=WIN_TOPICS_LOW, hi=WIN_TOPICS_HIGH),
        TOPICS_OPTS,
    ) or {}
    win_topics = _dedupe_topics(a1.get("topics", []))[:WIN_TOPICS_HIGH + 2]

    # A2 — sections for this window, conditioned on its topics (retry once if under-segmented)
    best_sections, best_summary = [], ""
    for _ in range(2):
        a2 = _llm(
            _SECTIONS_SYSTEM,
            _SECTIONS_TMPL.format(transcript=win_text, idx=idx, total=total,
                                  topic_list=", ".join(win_topics) if win_topics else "(none)",
                                  min_s=WIN_MIN_SECTIONS, max_s=WIN_MAX_SECTIONS),
            SECTIONS_OPTS,
        ) or {}
        secs = [s for s in a2.get("sections", []) if isinstance(s, dict)][:WIN_MAX_SECTIONS]
        if len(secs) > len(best_sections):
            best_sections = secs
            best_summary = (a2.get("video_summary") or "").strip() or best_summary
        if len(best_sections) >= WIN_MIN_SECTIONS:
            break
    return win_topics, best_sections, best_summary


def _reduce_topics(topic_counts, n_win):
    """A3 REDUCE — merge per-window topics (with frequencies) into a canonical global set.

    Falls back to a plain frequency-ordered dedupe if the LLM reduce fails.
    """
    ordered = sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    if n_win == 1 or len(ordered) <= 1:
        return _dedupe_topics([t for t, _ in ordered])[:MAX_TOPICS_GLOBAL]

    raw_block = "\n".join(f'- "{t}" (in {c} excerpt{"s" if c != 1 else ""})'
                          for t, c in ordered)
    data = _llm(
        _REDUCE_SYSTEM,
        _REDUCE_TMPL.format(n_win=n_win, raw_block=raw_block, max_t=MAX_TOPICS_GLOBAL),
        REDUCE_OPTS,
    ) or {}
    merged = _dedupe_topics(data.get("topics", []))[:MAX_TOPICS_GLOBAL]
    # Guard: if the model collapsed too aggressively (or failed), keep the frequency list.
    if len(merged) < max(2, len(ordered) // 4):
        return _dedupe_topics([t for t, _ in ordered])[:MAX_TOPICS_GLOBAL]
    return merged


def _segment_whole_gemini(full_text):
    """Pass A (Gemini) — segment the WHOLE transcript in ONE call. None if it fails.

    Returns {video_summary, topics, sections, n_windows:1} with sections tagged _window=0
    so split_into_sections() (a global marker search) still works. Topic/section counts
    scale with transcript length (more words ⇒ ask for more nodes), capped at the globals.
    """
    n_words = len(full_text.split())
    # Scale targets with length, bounded by the global caps. Roughly: ~1 topic / 1k words
    # and ~1 section / 800 words, within sane floors/ceilings.
    hi_t = max(WIN_TOPICS_HIGH, min(MAX_TOPICS_GLOBAL, n_words // 1000))
    lo_t = max(WIN_TOPICS_LOW, hi_t // 2)
    hi_s = max(WIN_MAX_SECTIONS, min(MAX_SECTIONS_GLOBAL, n_words // 800))
    lo_s = max(WIN_MIN_SECTIONS, hi_s // 2)

    # Give Gemini a generous output budget — many sections + premises/conclusions.
    data = llm_json(
        _WHOLE_SEGMENT_SYSTEM,
        _WHOLE_SEGMENT_TMPL.format(transcript=full_text, lo_t=lo_t, hi_t=hi_t,
                                   lo_s=lo_s, hi_s=hi_s),
        max_tokens=8192, temperature=0.2, qwen_options=SECTIONS_OPTS,
    )
    if not data:
        return None
    topics = _dedupe_topics(data.get("topics", []))[:MAX_TOPICS_GLOBAL]
    sections = [{**s, "_window": 0} for s in data.get("sections", [])
                if isinstance(s, dict)][:MAX_SECTIONS_GLOBAL]
    if not topics or not sections:
        return None
    return {
        "video_summary": (data.get("video_summary") or "").strip(),
        "topics": topics,
        "sections": sections,
        "n_windows": 1,
    }


def llm_segment(full_text):
    """Pass A — Gemini whole-transcript (single call) when available, else WINDOWED qwen.

    Gemini path: send the ENTIRE transcript in ONE call (no windowing — Gemini handles
    ~1M tokens). qwen fallback path (below): map-reduce over ~6k-word windows because
    qwen3:1.7b's 40960-token window can't hold a 50k-word transcript.
      MAP: per window, A0 plan -> A1 topics -> A2 sections.
      REDUCE: A3 merges per-window topics into a canonical global set; ordered per-window
      sections are concatenated. Counts scale with length (a longer transcript = more windows).
    """
    # Prefer a single whole-transcript Gemini call when Gemini is live and not cooling down.
    if _gemini_available() is not None and time.time() >= _gemini_cooldown_until:
        print("  🪟 Pass A — Gemini whole-transcript (single call, no windowing)..."
              + " " * 6, end="\r")
        whole = _segment_whole_gemini(full_text)
        if whole:
            print(f"  🧠 Pass A — Gemini whole-transcript: {len(whole['topics'])} topics · "
                  f"{len(whole['sections'])} sections" + " " * 12)
            return whole
        print("  ⚠️  Pass A — Gemini whole-transcript failed; using windowed fallback."
              + " " * 6)

    windows = _windows(full_text)
    n_win = len(windows)
    print(f"  🪟 Pass A — {n_win} window(s) "
          f"(~{WINDOW_WORDS}w each, {WINDOW_OVERLAP_WORDS}w overlap)" + " " * 12)

    topic_counts = {}
    all_sections = []
    summaries = []
    for wi, win in enumerate(windows, start=1):
        print(f"  🪟 Pass A — window {wi}/{n_win}: planning + segmenting...", end="\r")
        win_topics, win_sections, win_summary = _segment_window(win, wi, n_win)
        for t in win_topics:
            topic_counts[t] = topic_counts.get(t, 0) + 1
        # tag each section with its window so split_into_sections can locate the marker
        # within the right window (markers may repeat across a long transcript)
        for s in win_sections:
            all_sections.append({**s, "_window": wi - 1})
        if win_summary:
            summaries.append(win_summary)
    all_sections = all_sections[:MAX_SECTIONS_GLOBAL]

    topics = _reduce_topics(topic_counts, n_win)
    # Video summary: the first window's summary is the best whole-video opener; if windowed,
    # note that the rest follows. (Kept to one sentence per the schema/renderer contract.)
    video_summary = summaries[0] if summaries else ""

    print(f"  🪟 Pass A — REDUCE: {len(topic_counts)} raw -> {len(topics)} canonical "
          f"topics · {len(all_sections)} sections" + " " * 8)
    return {
        "video_summary": video_summary,
        "topics": topics,
        "sections": all_sections,
        "n_windows": n_win,
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

    # B0 — plan: which concrete facts are worth extracting (CoT scaffold)
    plan = _llm(
        _DRAFT_PLAN_SYSTEM,
        _DRAFT_PLAN_TMPL.format(title=section_title, section=section_text),
        DRAFT_PLAN_OPTS,
    ) or {}
    plan_block = _fmt_plan(plan)

    # B1 — draft candidates (up to 8), building on the plan
    draft = _llm(
        _DRAFT_SYSTEM,
        _DRAFT_TMPL.format(title=section_title, topic_list=tl, section=section_text,
                           plan=plan_block),
        DRAFT_OPTS,
    ) or {}
    draft_claims = _snap_claims(draft.get("claims", []), topic_list, limit=8)
    entities = [e for e in draft.get("entities", []) if isinstance(e, dict)][:6]
    triples  = _clean_triples(draft.get("triples", []))

    # B2 — verify + refine against the transcript section (self-refine step)
    claims = draft_claims
    if draft_claims:
        verified = _llm(
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

    # C0 — plan: which catalog section types these claims support (CoT scaffold)
    plan_data = _llm(
        _STRUCT_PLAN_SYSTEM,
        _STRUCT_PLAN_TMPL.format(topic=topic, claims_block=claims_block, catalog=catalog,
                                 max_sec=max_sec),
        STRUCT_PLAN_OPTS,
    ) or {}
    planned = [p for p in plan_data.get("plan", []) if isinstance(p, dict)
               and (p.get("type") or "").strip() in valid_types]
    plan_block = ("\n".join(f"- {p['type'].strip()} — {(p.get('why') or '').strip()}"
                            for p in planned) or "(plan unavailable — choose from the catalog)")

    # C1 — fill the chosen section types from the claims only
    data = _llm(
        _STRUCTURE_SYSTEM,
        _STRUCTURE_TMPL.format(topic=topic, claims_block=claims_block, catalog=catalog,
                               plan=plan_block, max_sec=max_sec, n_claims=len(claim_texts)),
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


def _window_char_offset(text, window_idx):
    """Approximate char offset where window `window_idx` begins, so a section marker is
    located near its own window rather than at the first (possibly repeated) match in a
    long transcript. Best-effort; 0 for the first window / unknown."""
    if not window_idx:
        return 0
    words = text.split()
    step = WINDOW_WORDS - WINDOW_OVERLAP_WORDS
    word_start = min(window_idx * step, max(0, len(words) - 1))
    if word_start <= 0:
        return 0
    # locate the start word's char position by re-joining the prefix
    prefix = " ".join(words[:word_start])
    return len(prefix)


def split_into_sections(text, outline_sections):
    """Split raw transcript into sections using each section's start_marker.

    Sections carry a `_window` index (from the windowed Pass A). The marker is searched
    starting from that window's approximate char offset so a phrase that recurs across a
    long transcript still anchors to the right place. Falls back to equal slices.
    """
    if not outline_sections:
        return _equal_slices(text, [{}])

    lower = text.lower()
    pairs = []
    for i, s in enumerate(outline_sections):
        marker = " ".join((s.get("start_marker") or "").split()[:8]).lower()
        if not marker:
            continue
        win_off = _window_char_offset(text, s.get("_window", 0))
        idx = lower.find(marker, win_off)
        if idx == -1:                      # fall back to a global search
            idx = lower.find(marker)
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


def _read_meta(transcript_path):
    """Read the <base>.meta.json sidecar (url/title/duration/transcribe time). {} if absent."""
    meta_path = Path(transcript_path).with_suffix(".meta.json")
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def update_topic_file(topic, facts_col, topic_connections=None):
    """Assemble a topic note from stored claims + Pass-D connection sentences.

    `topic_connections` maps topic name -> list of connection dicts
    {sentence, kind, with, cross_source}; rendered as a `## Connections` section of plain
    natural-language sentences (NOT arrow-triples). When omitted (e.g. during the extract
    phase before Pass D runs), the Connections section is simply left out and filled in on
    the connect pass.

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

    # Connections — plain natural-language sentences from the LLM Pass-D analysis. These
    # replace the old arrow-triple `## Relationships` block (too specific, no insight, wrong
    # syntax). Each sentence puts the verb inline and captures a real cross/intra-source
    # relationship a chatbot can extrapolate from.
    conns = (topic_connections or {}).get(topic, [])
    if conns:
        lines += ["", "## Connections"]
        for c in conns:
            sent = (c.get("sentence") or "").strip()
            if not sent:
                continue
            other = (c.get("with") or "").strip()
            tag = f" _(connects to: {other})_" if other else ""
            lines.append(f"- {sent}{tag}")

    n_conn = len([c for c in conns if (c.get("sentence") or "").strip()])
    lines += [
        "",
        "---",
        f"_Topic appears in {len(sources_set)} source(s) · "
        f"{len(claims)} claim(s) · {n_conn} connection(s)_",
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
# Pass D — LLM connection analysis across nodes (task 2)
# ---------------------------------------------------------------------------

def _topic_profiles(facts_col):
    """Return {topic: {"claims": [...], "sources": set, "centroid": np.array}} for every
    topic in the facts collection. The centroid is the mean of the topic's claim
    embeddings — a single vector representing the node for similarity clustering."""
    import numpy as np
    res = facts_col.get(include=["documents", "metadatas", "embeddings"])
    docs   = res.get("documents") or []
    metas  = res.get("metadatas") or []
    embs   = res.get("embeddings")
    embs   = embs if embs is not None else []
    profiles = {}
    for i, (doc, meta) in enumerate(zip(docs, metas)):
        topic = (meta.get("topic") or "").strip()
        if not topic:
            continue
        p = profiles.setdefault(topic, {"claims": [], "sources": set(), "_embs": []})
        p["claims"].append({"text": doc, "source": meta.get("source", "?")})
        p["sources"].add(meta.get("source", "?"))
        if i < len(embs) and embs[i] is not None and len(embs[i]):
            p["_embs"].append(np.asarray(embs[i], dtype=float))
    for t, p in profiles.items():
        if p["_embs"]:
            c = np.mean(np.stack(p["_embs"]), axis=0)
            n = np.linalg.norm(c)
            p["centroid"] = c / n if n else c
        else:
            p["centroid"] = None
        del p["_embs"]
    return profiles


def _build_suites(profiles):
    """Greedy clusters of related topics by centroid cosine distance.

    Each topic seeds a suite of its nearest neighbours within SUITE_DISTANCE (cosine),
    capped at SUITE_MAX_SIZE. Cross-source pairs are favoured by sorting neighbours so
    different-source topics come first. Duplicate suites (same member set) are dropped.
    """
    import numpy as np
    names = [t for t, p in profiles.items() if p.get("centroid") is not None]
    if len(names) < SUITE_MIN_SIZE:
        return []
    cents = {t: profiles[t]["centroid"] for t in names}

    def cos_dist(a, b):
        return 1.0 - float(np.dot(cents[a], cents[b]))

    suites, seen_keys = [], set()
    for seed in names:
        neighbours = []
        for other in names:
            if other == seed:
                continue
            d = cos_dist(seed, other)
            if d <= SUITE_DISTANCE:
                cross = bool(profiles[seed]["sources"] & profiles[other]["sources"]) is False
                # sort key: cross-source first (0), then nearer
                neighbours.append((0 if cross else 1, d, other))
        if not neighbours:
            continue
        neighbours.sort()
        members = [seed] + [n[2] for n in neighbours[:SUITE_MAX_SIZE - 1]]
        key = frozenset(members)
        if len(members) < SUITE_MIN_SIZE or key in seen_keys:
            continue
        seen_keys.add(key)
        suites.append(members)
    return suites


def _suite_block(members, profiles, max_claims=5):
    """Render a suite of topics + their claims (with source tags) for the Pass-D prompt."""
    out = []
    for t in members:
        p = profiles[t]
        srcs = ", ".join(sorted(p["sources"]))
        out.append(f'TOPIC "{t}"  (source: {srcs})')
        for c in p["claims"][:max_claims]:
            out.append(f"  - {c['text']}")
    return "\n".join(out)


def analyze_connections(facts_col):
    """Pass D — cluster topics into suites and use the LLM (CoT) to produce natural-language
    connection sentences between nodes, especially across sources.

    Returns (per_topic, edges):
      per_topic : {topic: [{sentence, kind, with, cross_source}, ...]}
      edges     : [{a, b, kind, cross_source, sentence}, ...]  (one per topic pair)
    """
    profiles = _topic_profiles(facts_col)
    suites = _build_suites(profiles)
    print(f"  🔎 Pass D — {len(profiles)} topics → {len(suites)} candidate suite(s)")

    per_topic = {}
    edges = []
    edge_seen = set()

    for si, members in enumerate(suites, start=1):
        print(f"  🔎 Pass D — suite {si}/{len(suites)} "
              f"({len(members)} nodes): reasoning...", end="\r")
        block = _suite_block(members, profiles)

        # D0 — plan the genuine relationships (CoT)
        plan = _llm(
            _CONNECT_PLAN_SYSTEM,
            _CONNECT_PLAN_TMPL.format(n=len(members), suite_block=block),
            CONNECT_PLAN_OPTS,
        ) or {}
        rels = [r for r in plan.get("relationships", []) if isinstance(r, dict)]
        if not rels:
            continue
        plan_json = json.dumps(rels, ensure_ascii=False)

        # D1 — write each relationship as a natural-language sentence
        written = _llm(
            _CONNECT_WRITE_SYSTEM,
            _CONNECT_WRITE_TMPL.format(n=len(members), suite_block=block, plan=plan_json),
            CONNECT_OPTS,
        ) or {}

        for c in written.get("connections", []):
            if not isinstance(c, dict):
                continue
            pair = [str(x).strip() for x in (c.get("topics") or []) if str(x).strip()]
            sentence = (c.get("sentence") or "").strip()
            kind = (c.get("kind") or "related").strip().lower()
            if len(pair) != 2 or not sentence:
                continue
            # snap topic names to the real ones in this suite (case-insensitive)
            snapped = []
            for name in pair:
                match = next((m for m in members if m.lower() == name.lower()), None)
                snapped.append(match)
            if not all(snapped) or snapped[0] == snapped[1]:
                continue
            a, b = snapped
            # reject lingering arrow-triple junk
            if "→" in sentence or "->" in sentence:
                continue
            cross = not (profiles[a]["sources"] & profiles[b]["sources"])
            ekey = frozenset((a, b))
            if ekey in edge_seen:
                continue
            edge_seen.add(ekey)
            edges.append({"a": a, "b": b, "kind": kind,
                          "cross_source": cross, "sentence": sentence})
            per_topic.setdefault(a, []).append(
                {"sentence": sentence, "kind": kind, "with": b, "cross_source": cross})
            per_topic.setdefault(b, []).append(
                {"sentence": sentence, "kind": kind, "with": a, "cross_source": cross})

    n_cross = sum(1 for e in edges if e["cross_source"])
    print(f"  🔗 Pass D — {len(edges)} connection(s) "
          f"({n_cross} cross-source)" + " " * 20)
    return per_topic, edges


def run_connection_pass():
    """Run Pass D over the existing knowledge base and rewrite notes + connections.json."""
    facts_col, _ = get_collections()
    if facts_col.count() == 0:
        print("⚠️  facts collection is empty — nothing to connect. Process transcripts first.")
        return
    per_topic, edges = analyze_connections(facts_col)

    save_json(CONNECTIONS_FILE, {
        "generated_at": datetime.now().isoformat(),
        "per_topic": per_topic,
        "edges": edges,
    })

    # Rewrite every topic note so the new `## Connections` section is filled in.
    topics = sorted({(m.get("topic") or "").strip()
                     for m in (facts_col.get(include=["metadatas"])["metadatas"] or [])
                     if (m.get("topic") or "").strip()})
    rewritten = sum(1 for t in topics if update_topic_file(t, facts_col, per_topic))
    rebuild_index()
    print(f"  ✅ Pass D complete — {len(edges)} edges, {rewritten} note(s) updated")


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
    t_start = time.time()

    # ── Pass A: SEGMENT (WINDOWED map-reduce — handles multi-hour transcripts) ──
    outline = llm_segment(text)
    topics  = outline["topics"]
    sections = split_into_sections(text, outline["sections"])
    if not (MIN_TOPICS <= len(topics) <= MAX_TOPICS_GLOBAL):
        print(f"  ⚠️  topic count {len(topics)} outside target — proceeding with {topics}")
    print(f"  🧭 Pass A — {len(sections)} sections · {len(topics)} canonical topics: "
          f"{', '.join(topics[:12])}{' …' if len(topics) > 12 else ''}" + " " * 8)

    facts_col, chunks_col = get_collections()

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

    # Assemble topic notes (only topics that produced claims). The `## Connections` section
    # is left empty here and filled by Pass D (run after all sources are in, so it can find
    # cross-source relationships). Notes are rewritten by run_connection_pass().
    final_topics = sorted(used_topics)
    print(f"  → Assembling {len(final_topics)} topic note(s)...")
    written = [t for t in final_topics if update_topic_file(t, facts_col)]

    process_seconds = round(time.time() - t_start, 1)
    meta = _read_meta(path)
    sources[name] = {
        "processed_at":      datetime.now().isoformat(),
        "video_summary":     outline["video_summary"],
        "word_count":        word_count,
        "window_count":      outline.get("n_windows", 1),
        "section_count":     len(sections),
        "claim_count":       claim_count,
        "entity_count":      entity_count,
        # task 4 — enrich source nodes: link, lengths, times
        "url":               meta.get("url", ""),
        "title":             meta.get("title", ""),
        "duration_seconds":  meta.get("duration_seconds"),
        "transcribe_seconds":meta.get("transcribe_seconds"),
        "process_seconds":   process_seconds,
        "topics":            written,
        "sections":          [
            {"title": (s.get("title") or f"Section {i+1}").strip(),
             "premise": (s.get("premise") or "").strip(),
             "conclusion": (s.get("conclusion") or "").strip()}
            for i, s in enumerate(sections)
        ],
    }
    save_json(SOURCES_FILE, sources)
    rebuild_index()

    print(f"  ✅ {name} complete — {process_seconds}s")


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

    # Pass D — runs AFTER every source is in so it can find cross-source relationships.
    print("\n🔗 Pass D — analyzing connections across all nodes...")
    run_connection_pass()
    _llm_summary()


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
    save_json(CONNECTIONS_FILE, {"per_topic": {}, "edges": []})

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
    elif arg == "--connect":
        run_connection_pass()
        _llm_summary()
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
        _llm_summary()


if __name__ == "__main__":
    main()
