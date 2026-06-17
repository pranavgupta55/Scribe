# .scribe-skills — Read me first

This directory is the durable knowledge base for the **graph rebuild** initiative (branch `graph-rebuild`). Every sub-agent dispatched during the rebuild reads at least one of these files. Read order, top-down:

1. **[PLAN.md](./PLAN.md)** — the comprehensive rebuild plan, phases, agent counts, cost budget, and open questions.
2. **[NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md)** — twenty failure modes catalogued with verbatim bad examples, positive signals, and the scoring matrix every node must pass.
3. **[CLAIM-DEFINITION.md](./CLAIM-DEFINITION.md)** — what a Claim is, what it isn't, the canonical glossary, and the claim-shape schema.
4. **[HIERARCHY.md](./HIERARCHY.md)** — the L0 Concept / L1 Framework / L2 Claim / L3 Example structure.

Borrowed skills (verbatim from [mattpocock/skills](https://github.com/mattpocock/skills)):

5. **[skills/ubiquitous-language.md](./skills/ubiquitous-language.md)** — DDD-style glossary discipline. The single most-load-bearing borrowed skill: it is the cure for "the founder did X".
6. **[skills/context-format.md](./skills/context-format.md)** — the CONTEXT.md format for canonical terms + aliases.
7. **[skills/architecture-language.md](./skills/architecture-language.md)** — the deletion test ("if I delete this node, does its content reappear elsewhere?") and depth/leverage/locality terms. Borrowed for thinking about whether a node earns its place.
8. **[skills/adr-format.md](./skills/adr-format.md)** — how we record durable design decisions in `docs/adr/` (created lazily). Each "hard to reverse, surprising, real trade-off" decision becomes an ADR.
9. **[skills/diagnose.md](./skills/diagnose.md)** — the feedback-loop discipline. Used in Phase 6 to make graph-quality eval deterministic and unattended-runnable, not vibes-based.

## What gets generated during the rebuild

```
.scribe-skills/
├── research/                        ← created during Phase 0
│   ├── 0a-research-01..10.md        ← one per web agent
│   ├── 0a-SYNTHESIS.md              ← synthesizer call
│   ├── 0b-diagnostic-corpus.jsonl   ← 240 labeled nodes
│   └── 6-eval-fail.md (if any)      ← if Phase 6 fails the cutoffs
├── FEW-SHOT.md                       ← 10 positive + 20 negative, written in Phase 0c
```

## How to navigate as a sub-agent

If you are a sub-agent reading this from a phase brief:

- **Phase 0a web agent**: read [PLAN.md §2 — Phase 0a](./PLAN.md). Your specific question is in your brief. Write to `.scribe-skills/research/0a-research-NN.md`.
- **Phase 0b diagnostic agent**: read [PLAN.md §2 — Phase 0b](./PLAN.md) + [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) §A and §C. Score the nodes you are given.
- **Phase 0c few-shot harvest agent**: read [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) + the merged 0b corpus. Pick canonical positive/negative examples.
- **Phase 1a cluster verification agent**: read [HIERARCHY.md §1](./HIERARCHY.md) + [skills/ubiquitous-language.md](./skills/ubiquitous-language.md) + [FEW-SHOT.md](./FEW-SHOT.md). Apply the verification rules in [PLAN.md §2 — Phase 1a](./PLAN.md).
- **Phase 3a extraction agent**: read [CLAIM-DEFINITION.md](./CLAIM-DEFINITION.md) + [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) §A + §C + [FEW-SHOT.md](./FEW-SHOT.md) + [HIERARCHY.md](./HIERARCHY.md).
- **Phase 4 connection agent**: read [CLAIM-DEFINITION.md §4](./CLAIM-DEFINITION.md) + [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) §A11-A12 + the new `CONN_PROMPT_V2.md` in your brief.
- **Phase 6 eval agent**: read [NODE-QUALITY-RUBRIC.md](./NODE-QUALITY-RUBRIC.md) §C. Score the sample you are given against the matrix.

## Working principles

- **Be opinionated** (from skills/ubiquitous-language). When two names exist for one concept, pick one and list the other as an alias-to-avoid.
- **The deletion test** (from skills/architecture-language). For every node: if I delete this, does its content reappear elsewhere? If yes, the node is shallow — merge or drop. If no, it's earning its place.
- **Don't paraphrase the rubric in your output** — apply it. The rubric is the eval surface, not the report surface.
- **Demote rather than reject** when uncertain. A weak claim becomes an example. A weak example becomes an implementation detail. Outright drops are reserved for content that fails to add information at any level.
- **Source-attribute every node**. No anonymous claims. Every entity is named, every time-anchor is present, every speaker is identified.
