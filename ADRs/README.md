# Architecture Decision Records

ADRs for Scribe — records of significant technical decisions including context, alternatives considered, and what happened during implementation.

ADRs are **immutable once accepted**. New decisions that change a prior choice create a new ADR and reference the old one (`Supersedes ADR-NNNN`).

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [0001](decisions/0001-rag-chat-architecture.md) | RAG chat: expanded system prompt, history compaction, adaptive retrieval | Accepted (D1/D2; D3 deferred) | 2026-06-20 |
| [0002](decisions/0002-ui-polish-and-gibberish-gate.md) | 3-col topbar, viewport-centered chat, narrow decomp strip, gibberish-query gate | Accepted | 2026-06-20 |
| [0003](decisions/0003-topbar-invisibility.md) | Topbar surface invisible (chrome-less, 48 px reserve retained) | Accepted | 2026-06-20 |
| [0004](decisions/0004-visual-bug-batch.md) | Visual-bug batch (round 1): chat clamp, decomp width, panel cap, empties, header | Accepted | 2026-06-20 |
| [0005](decisions/0005-visual-bug-batch-two.md) | Visual-bug batch (round 2): drop chat shim, transform-immune bento, proportional strip, overflow | Accepted | 2026-06-21 |
| [0006](decisions/0006-v2-viewer-adaptation-and-source-search.md) | v2 viewer fixes: sidebar labels, source pills, ordered/nested markdown, level filter, source-search bar | Accepted | 2026-06-21 |
| [0007](decisions/0007-exec-python-in-serve-sh.md) | `exec python3` in `serve.sh` so the wrapper bash PID doesn't linger | Accepted | 2026-06-21 |
| [0008](decisions/0008-repo-restructure-and-gitignore-cleanup.md) | Repo restructure: consolidate ADR dirs, gitignore heavy phase artifacts, setup.sh pulls qwen3-embedding:8b | Accepted | 2026-06-21 |

## Conventions

- Numbering: 4-digit zero-padded, monotonic by acceptance date (`0001`, `0002`, …).
- Sub-decisions inside one ADR are labelled `D1 / D2 / D3` and may be referenced inline in code (e.g. `// ADR-0004 D2`).
- Both `ADR-0006` and `ADR 0004 D2` forms exist in code; either is acceptable.
