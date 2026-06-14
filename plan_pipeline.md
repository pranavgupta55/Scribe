# Scribe pipeline redesign — plan (sub-agent A: tasks 4, 5, 6)

## Diagnosis (why the current output is weak)

Reading the 5 current notes against the transcript surfaces three concrete failures:

1. **Vague / inverted claims (task 4).** Roughly half the stored claims fail the rubric in
   `evals/claim_evals.md`: dangling referents ("the example of a text message…"), bare
   labels ("the Outreach Specialist is called a database reactivation"), generic filler
   ("AI is strategically used … to save time"), and at least one outright **inversion of
   the source** ("The five AI workers … can be replaced by cheaper freelancers" — the
   speaker says the *opposite*). Cause: a single extraction call asked the 1.7B model to
   simultaneously find, decontextualize, ground, and topic-tag claims. Too much at once.

2. **Too few nodes (task 5).** Only 5 topics for a ~6000-word, ~25-min video. Targets were
   `4-8` topics / `5-10` sections. The video clearly has more distinct ideas (the 5 named
   workers, niche selection, franchise referrals, cold-outreach-vs-ads, the ad setup, the
   bucket model, proof-of-revenue).

3. **Flat note structure (task 6).** Every note is just `> headline` + `## Claims` +
   `## Relationships`. No synthesized conclusions, no per-topic structure. A reader gets a
   bullet dump, not an argument.

## Research grounding (small-LLM technique)

- **Prompt chaining / decomposition** — break one hard call into single-objective subtasks;
  each isolates one goal and reduces cognitive load, and chaining beats a monolithic
  draft+critique+refine instruction in controlled summarization tests.
  ([Prompting Guide](https://www.promptingguide.ai/techniques/prompt_chaining),
  [learnprompting decomposition](https://learnprompting.org/docs/advanced/decomposition/introduction),
  [getmaxim](https://www.getmaxim.ai/articles/prompt-chaining-for-ai-engineers-a-practical-guide-to-improving-llm-output-quality/))
- **SELF-REFINE** — same model as generator → feedback → refiner improves output without
  retraining. We use a lighter two-step (draft → verify/fix against transcript).
  ([Self-Review framework, arXiv 2507.05598](https://arxiv.org/pdf/2507.05598),
  [text-to-table iterative refinement, arXiv 2508.08653](https://arxiv.org/pdf/2508.08653))
- **Few-shot reinforces task logic; structured form guards grounding/hallucination** — show
  graded examples; state JSON intent explicitly; repair minor JSON syntax rather than
  trusting the model.
  ([structured JSON, DEV](https://dev.to/rishabdugar/crafting-structured-json-responses-ensuring-consistent-output-from-any-llm-l9h),
  [hallucination survey, Frontiers](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1622292/full),
  [structured-output benchmark, arXiv 2501.10868](https://arxiv.org/html/2501.10868v1))

Takeaway for a 1.7B model: **many short, single-objective calls + concrete graded few-shot
+ an explicit verify-against-source pass** is the highest-leverage change.

## New multi-step pipeline

### Pass A — SEGMENT (now 2 chained calls)
- **A1 `topics`** — whole transcript → ONLY the canonical topic list. One job. Target
  **10-16** topics (raise from 4-8), 1-3 words, Title Case, deduped, no near-synonyms.
- **A2 `sections`** — whole transcript + the A1 topics → `video_summary` + sections, target
  **8-14** (raise from 5-10), each `{title, start_marker, premise, conclusion}`.
Splitting topics from sections stops the model from collapsing them into 5 of each.

### Pass B — EXTRACT (now 2 chained calls per section)
- **B1 draft** — section text → candidate `claims` + `entities` + `triples`. Few-shot
  GOOD/BAD contrast baked in. Allowed up to 8 candidate claims (over-generate).
- **B2 verify+refine** — section text + B1 claims → for each candidate, apply the 5-point
  rubric, then **drop** vague/ungrounded ones and **rewrite** salvageable ones to add
  transcript context. This is the SELF-REFINE step and the main quality lever. Returns the
  kept/rewritten claims (max 6), each topic-tagged.

### Note assembly — adds a structured-conclusions call (Pass C, per topic)
`update_topic_file` stays deterministic for Claims/Relationships, but now first calls
**C1 `structure`**: given the topic's kept claims, the model (a) picks the **3-5** most
applicable section types from the catalog by explicit criteria, then (b) fills each ONLY
from the claims/transcript. Output is standard Markdown `##` sections inserted above
`## Claims`. If C1 fails, the note still renders (claims + relationships) — graceful
degradation.

## Section-type catalog (task 6) + selection criteria

Model is given all of these and told to pick the 3-5 that the topic's claims actually
support (skip any it would have to invent to fill):

1. **Key Takeaway** — the single most important conclusion. *Include almost always.*
2. **Key Numbers** — the concrete metrics/stats. *Include if ≥2 numbers exist.*
3. **How-To / Method** — ordered steps the speaker gives. *Include if the topic describes a procedure.*
4. **Evidence & Examples** — named cases/testimonials backing a claim. *Include if a named example exists.*
5. **Why It Works** — the stated mechanism/causal reason. *Include if the speaker explains a "because".*
6. **Implications** — what follows for the practitioner. *Include if the claims imply an action.*
7. **Actionable Advice** — explicit do-this directives. *Include if the speaker gives instructions.*
8. **Contradictions / Tensions** — claims in tension, or a "most people do X but…" reversal. *Include if present.*
9. **Caveats & Limits** — stated conditions, exceptions, what won't work. *Include if the speaker hedges.*
10. **Open Questions** — what the transcript leaves unanswered. *Include sparingly, only if genuinely raised.*
11. **Notable Quotes** — a verbatim memorable line. *Include if a quotable line exists.*

Hard rule repeated in the prompt: **fill a section only from transcript content; if you'd
have to invent to fill it, don't pick it.**

## New note format (standard Markdown — renderer-safe)

```
# <Topic>

> <headline: best one-line takeaway>

## Key Takeaway
- ...

## Key Numbers
- ...

(3-5 chosen sections total, from the catalog)

## Claims
- <claim> — [<full_source_filename> § "<section title>"]

## Relationships
- subj → predicate → obj

---
_Topic appears in N source(s) · M claim(s) · K relationship(s)_
_Sources: <full filenames>_
```

Only `##`, `-`, `>`, `**bold**`. Full source filenames kept (B shortens at render).
`export_graph.py` already passes the whole body through as the node `description`, and its
`read_topic_md` strips H1 + footer regardless of which `##` headings appear — so **no
export_graph.py change is required**; it tolerates the new headings already.

## Ollama params (unchanged philosophy: explicit ctx, low temp, json mode)
- A1/A2 (segment): `num_ctx 16384`, `num_predict 1024` (A1) / `2048` (A2), temp 0.2.
- B1 (draft): `num_ctx 8192`, `num_predict 1536`, temp 0.2.
- B2 (verify): `num_ctx 8192`, `num_predict 1536`, temp 0.1 (more deterministic).
- C1 (structure): `num_ctx 8192`, `num_predict 1536`, temp 0.3.
- All keep `top_p 0.8, top_k 20, repeat_penalty 1.05`, `format="json"`, `/no_think`.
- `EMBED_MAX_CHARS = 6000` guard preserved (nomic 2048-token window).

## Schema changes
- `sources.json` section count rises; nothing breaks (reader is tolerant).
- `facts` collection metadata unchanged (claim/topic/triples/source/section_title).
- Topic notes gain `##` sections — additive, renderer-tolerant.

## Few-shot examples
Sourced from `evals/claim_evals.md` (G1, G2, G3 positive; B2, B3, B4 negative),
compressed into the B1 draft and B2 verify prompts.

## Implementation checklist
1. [x] Read code, transcript, current notes.
2. [x] Web-search techniques; write `evals/claim_evals.md`.
3. [x] Write this plan.
4. [x] Raise topic/section targets; split Pass A into A1+A2 (+retry on under-segmentation).
5. [x] Split Pass B into B1 draft + B2 verify/refine with few-shot.
6. [x] Add Pass C structure call + new note assembly in `update_topic_file`.
7. [x] Long system prompts + heavy transcript framing (markers, grounding laws, few-shot).
8. [x] `python3 process.py --rebuild`; inspected every note (see results below).
9. [x] `python3 export_graph.py` builds clean (10 nodes, 45 edges, PCA ok).
10. [x] Summary + counts.

## Results (final rebuild)
- 10 canonical topics; 9 sections; 22 claims; 9 topic notes written.
- Each note has a synthesized headline + 1-5 chosen structured sections + cited Claims +
  Relationships. Number-grounding guard dropped all invented statistics from C1.
- Anti-hallucination guard added: a C1 bullet containing a number absent from every claim
  is dropped (`_numbers_in`). Catches the "invented stat / fake case study" failure mode.
- export_graph.py needed NO change: `read_topic_md` strips H1 + footer and passes the rest
  (including the new `##` sections) through as the node description.
- Residual (model-inherent, isolated): occasional non-numeric phrasing slips the verifier
  can't catch (e.g. "GBT (Generative Business Tool)" expansion; one 62% phrasing inversion
  in missed_call_text_back). Acceptable for a 1.7B model; tightening further risks the gains.
