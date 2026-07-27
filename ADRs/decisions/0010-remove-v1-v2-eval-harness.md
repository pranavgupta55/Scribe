# 0010: Remove the `eval_v1_vs_v2.py` Harness — No Judge, No Rubric, No CI

## Status
Accepted

## Date
2026-07-26

## Context

ADR 0009 shipped a graph-augmented RAG v2 pipeline plus a Python script,
`scripts/eval_v1_vs_v2.py`, intended as an eval harness for v1-vs-v2 answer
quality. In practice, the script was never a real eval:

- **No judge.** It posts each query to `POST /api/chat` with `use_v2=false`
  and again with `use_v2=true`, streams the SSE, and dumps the first ~400
  chars of each answer + retrieval stats to a JSON file. There is no scoring
  step. Comparing v1 vs v2 required a human to eyeball the pairs.
- **No CI.** There is no `.github/workflows/` directory in the repo. The
  script was only ever runnable by starting `server.py` locally in one
  terminal and running the script in another. It was never wired to run on
  push, on schedule, or on release.
- **Never actually run end-to-end.** The output path
  `.forge_scratch/scribe_rag_v2/eval_v1_v2.json` has never existed. The
  script sat in the repo as a placeholder for a Phase 6 quality eval that
  was always described as "deferred."
- **Answer LLM is not the right axis.** Even if the script had been run,
  the answer LLM is whatever `server.py`'s fallback chain picks (Gemini 2.5
  Flash → Gemini 2.0 Flash → local `qwen3:1.7b`). The interesting quality
  signal from RAG v2 is *retrieval quality* (did the right facts / chunks
  surface, was the right contradiction highlighted), not answer-LLM
  behavior. A side-by-side answer dump captures the wrong thing.

The pipeline behind ADR 0009 is otherwise complete: `retrieval_v2.py`,
`prompt_v2.py`, the `use_v2` body flag, and the rebuilt ChromaDB v2
collections are all live. The eval harness is the only dead component.

Keeping a script named "eval" in `scripts/` signals to future readers that
v1-vs-v2 quality is being measured. It is not. Better to remove the
signal than to maintain a script that lies about what the codebase does.

## Decision Drivers

- **Should:** Remove dead code that misrepresents what the repo does.
- **Should:** Keep the ADR trail so future me / future us can see *why* the
  script was removed rather than just noticing it disappeared.
- **Should not:** Delete the *idea* of a quality eval. A real eval is
  still valuable; it just needs a real design (rubric, judge, dataset).
  This ADR removes the stub, not the goal.

## Decision

1. **Delete `scripts/eval_v1_vs_v2.py`.** Already done in commit
   `d782a959` on 2026-07-26.
2. **Strip references** to the eval harness from `docs/RAG_V2.md` and
   from ADR 0009. Where the docs previously described a §9 "Eval Harness"
   section and §11 "Phase 6 quality eval" deferred item, replace with a
   pointer to this ADR (0010) so the removal is discoverable rather than
   silent.
3. **Do not replace it right now.** A real answer-quality eval for a
   graph-augmented RAG system is a nontrivial design problem (rubric
   dimensions, judge model, ground-truth answer set, retrieval-vs-answer
   attribution). Land the design when we actually need to measure quality,
   not before.

## Alternatives Considered

### A. (Chosen) Delete + document

Remove the script. Add ADR 0010 explaining the removal. Point stripped
doc sections at this ADR.

**Pros:** Repo state matches reality. Removal is discoverable.
**Cons:** Requires touching two docs plus one new ADR.

### B. Keep the script but rename it "smoke test"

Retitle `eval_v1_vs_v2.py` as e.g. `smoke_test_chat.py`, keep the code,
adjust docs to describe it as a smoke test not a quality eval.

**Pros:** Preserves the SSE-consumption plumbing (which was fine).
**Cons:** The script does not check anything; it just prints. There is no
smoke test either. A smoke test is code that fails when something breaks;
this is just a curl-in-a-loop.

**Rejected.** No component of the current script is worth keeping in
its current shape. If we want a smoke test later, we write one against
a specific assertion (e.g., "at least N facts surface for query X").

### C. Delete the script silently, do not write an ADR

Just `git rm` the file and move on.

**Pros:** Fewer docs to maintain.
**Cons:** The design in ADR 0009 documents an eval harness that does not
exist. Anyone reading 0009 later would look for `eval_v1_vs_v2.py`, not
find it, and have to reconstruct why it was removed from `git log`. The
whole point of ADRs is that reversals are as legible as originals.

**Rejected.** The cost of one small ADR is low; the readability win is
worth it.

### D. Build a real eval now instead

Design a rubric (retrieval precision / recall, prompt-quality dims,
answer-quality dims), pick a judge model, curate a ground-truth query
set with expected fact IDs, and implement `eval_rag_v2.py` from scratch.

**Pros:** Ships a real measurement.
**Cons:** Cost. The user (Pranav) said explicitly: "I'm not sure how to
write a good eval suite for something like this in the first place." That
is exactly the right instinct — a bad eval is worse than no eval because
you optimize for the wrong thing. Real evals for graph-RAG systems need
domain-specific ground truth (per-query expected claim IDs, per-query
expected contradiction pairs) that we do not have.

**Deferred.** Do not conflate cleaning up the stub with designing the
real thing. Land the real one when there's a specific quality question
to answer (e.g., "did adding contradictions expansion actually help?").

## Consequences

**Positive:**
- `scripts/` no longer contains code that pretends to do something it
  doesn't.
- ADR 0009 stops overpromising; the "Eval Harness" section now points to
  this ADR instead.
- Future eval work starts from a clean slate — no legacy script to feel
  obligated to extend.

**Negative:**
- No quality signal on RAG v2 right now. We can eyeball answers
  interactively via the chat UI (v1 vs v2 by toggling the body flag), but
  there is no batched comparison. Acceptable — the pipeline is small
  enough to spot-check by hand until we have a real reason to measure.

**Risks:**
- If someone rediscovers the `t-fokp1z` "Phase 6 eval" task, they may try
  to reimplement the deleted script. Mitigated by this ADR: the task
  description should be updated to point here.

## Implementation Plan

- [x] `git rm scripts/eval_v1_vs_v2.py` (commit `d782a959`).
- [ ] Add this ADR (`adrs/decisions/0010-remove-v1-v2-eval-harness.md`).
- [ ] Add index row in `adrs/README.md`.
- [ ] Strip §9 (Eval Harness) from `docs/RAG_V2.md`; leave a one-line
      pointer to ADR 0010. Renumber remaining sections.
- [ ] Strip Phase-6 eval row from `docs/RAG_V2.md` §11 deferred table;
      leave a note referencing this ADR.
- [ ] Strip eval-script references from the header of ADR 0009 (the
      "Related code" list) and any body text that mentions the harness;
      point to this ADR.
- [ ] Update the deferred task `t-fokp1z` (Phase 6 eval) — either close
      it (superseded by this ADR) or update its description to reference
      0010 as the current position.

## Build Log

```
EVENT
problem: scripts/eval_v1_vs_v2.py in the repo but never run, no judge, no CI, mispresented v1-vs-v2 quality as measurable when it wasn't
solution: git rm the script, write ADR 0010, strip doc/ADR refs
tests: n/a (removal only); ADR 0009 still coherent after edits; repo grep for eval_v1_vs_v2 returns 0 hits
outcome: repo no longer claims to have an eval it doesn't have
```

## Related

- **Supersedes deferred items in:** [`0009-graph-augmented-rag.md`](0009-graph-augmented-rag.md) — specifically the "Phase 6 quality eval" deferred item and the "Related code" reference to `eval_v1_vs_v2.py`.
- **Design doc:** [`../../docs/RAG_V2.md`](../../docs/RAG_V2.md) — updated in the same PR to remove eval-harness section.
