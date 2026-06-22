# 0008: Repo Restructure and Gitignore Cleanup

## Status
Accepted

## Date
2026-06-21

## Context

After seven ADRs and a major v1→v2 graph rebuild, an audit surfaced multiple state inconsistencies in the repo:

1. **Two ADR directories.** `docs/adr/0001–5` (the original UI/RAG batch from 06-20→21) coexisted with `ADRs/decisions/0001–2` (newer infrastructure ADRs from this session). The numbers collided across the two dirs, and `docs/adr/` predated the `adr` skill's `ADRs/decisions/` convention.
2. **Heavy regenerable artifacts tracked.** `.scribe-skills/phase{1a,3b,4}/` had ~240 MB of intermediate caches under version control: `embeddings.npy` (47 MB), `candidate_pairs.json` (22 MB), 98× per-batch input JSONs (up to 6.8 MB each). All deterministically rebuildable from the per-source `extracted/*.json` plus the scripts in `.scribe-skills/scripts/`.
3. **`setup.sh` missing the v2 embedding model.** `ARCHITECTURE.md` documents `qwen3-embedding:8b` (4.7 GB) as the Phase-1a/3b/4 embedder, but `setup.sh` only pulled `qwen3:1.7b` and `nomic-embed-text`. Fresh clones could complete `setup.sh` and then fail Phase 3b prepass with `model 'qwen3-embedding:8b' not found`.
4. **On-disk orphans.** `.haiku/` (13 MB, the pre-graph-rebuild Hormozi batch artifacts, gitignored), `knowledge/sources.json.bak` (36 KB, gitignored), and `.queue/__pycache__/` (12 KB) lingered locally.
5. **Stale `graph-rebuild` branch.** 0 commits ahead of `main`, 24 behind, fully merged on 06-17. Still alive both locally and at `origin/`.
6. **`ADRs/README.md` was a 7-line stub.** Did not list or summarize any of the seven existing ADRs, so navigating the record required directory listing.

This ADR captures the cleanup performed in one cycle: it is a maintenance decision, not a feature, but the scope is significant enough that a future engineer asking "why are old ADRs renumbered and most of `.scribe-skills/phase*/` gitignored?" deserves a single citable answer.

## Decision Drivers

- **Must:** No ADR numbering collisions; single canonical ADR directory.
- **Must:** Fresh `git clone && bash setup.sh` produces a runnable v2 pipeline without manual model pulls.
- **Should:** Repo size stay bounded — heavy phase artifacts get rebuilt cheaply on demand and shouldn't bloat clones.
- **Should not:** Break inline `// ADR-NNNN` references already embedded in `graph/graph.js` + `graph/index.html` + `serve.sh`.
- **Should not:** Drop *useful* `.scribe-skills/` content. Per-batch scored outputs, merged claim lists, dispatch metadata, and source-list snapshots ARE the audit trail of how the v2 graph was built and are kept.

## Decision

Six concrete moves, captured together because they share the cleanup motivation:

1. **ADR consolidation.** Move `docs/adr/0001–5` into `ADRs/decisions/` (numbers preserved). Renumber the two ADRs that landed in `ADRs/decisions/` this session as `0006-v2-viewer-adaptation-and-source-search.md` and `0007-exec-python-in-serve-sh.md` (monotonic by push date). Update the two inline code references (`graph/graph.js:221` `ADR-0001 → ADR-0006`; `serve.sh:29` `ADR-0002 → ADR-0007`). Delete the now-empty `docs/adr/` directory.

2. **`ADRs/README.md` expansion.** Replace the 7-line placeholder with a one-table index listing all eight ADRs (title, status, date) plus a brief conventions section.

3. **Gitignore heavy phase artifacts.** Add patterns covering `.scribe-skills/phase*/{embeddings,claim_embeddings,concept_embeddings,candidate_pairs,candidates,candidates_top}.{npy,json}` and `.scribe-skills/phase*/{batches,classify_batches}/batch_*.json`. `git rm --cached` the matches (~100 MB removed from the working tree's history-from-here-forward — old history retains them, but new clones get a smaller checkout). Lightweight artifacts (`scored_*.json`, `merged_claims.jsonl`, `dispatch.json`, `source_list*.json`, all phase MD docs) stay tracked.

4. **`setup.sh` adds the v2 embedder.** Append `OLLAMA_MODELS="${MODELS_DIR}/ollama" ollama pull qwen3-embedding:8b` after the existing pulls, with a comment noting the model is only needed by the v2 rebuild path. Update `SETUP.md`'s model size table accordingly (Ollama footprint becomes ~6.4 GB).

5. **Delete on-disk orphans.** `rm -rf .haiku/ knowledge/sources.json.bak .queue/__pycache__/`. Already gitignored; this is purely local disk reclamation.

6. **Delete stale `graph-rebuild` branch.** Local + remote. Branch is fully merged; keeping it stale invites future "is this still active?" cycles.

## Alternatives Considered

### A. (Chosen) One cleanup ADR + three commits
Single ADR captures the rationale for all six moves; three commits (ADR consolidation, gitignore + setup, ADR record) keep history readable. Tradeoff accepted: the ADR runs longer than usual since it covers six items.

### B. Six ADRs, one per move
Rejected. Each individual move is trivial — its own ADR would be 50 words of context for 20 words of decision. The cluster has a single coherent motivation (cleanup after the v1→v2 rebuild settled) and reads better together.

### C. Skip the ADR; just commit the moves
Rejected. The renumbering of 0001/0002 → 0006/0007 changes the meaning of every inline `// ADR-0001` comment in the codebase. Without a written record, a future reader can't tell whether `ADR-0006` was a typo or a deliberate rename.

### D. Renumber the OLD ADRs (0001-5) instead of the new ones
Rejected. The old ADRs are referenced by `D1/D2/D3` sub-decision labels inside `graph/graph.js` and `graph/index.html` in ~10 places. Renumbering them would force re-editing every comment; renumbering the two NEW ADRs only touches two files.

## Consequences

**Positive:**
- One ADR directory; numbering monotonic by acceptance date.
- Fresh clones can run the full v2 pipeline after `bash setup.sh` with no manual model pulls.
- `git clone` ships ~100 MB lighter (heavy artifacts no longer in HEAD).
- `ADRs/README.md` is now a navigable index.
- Disk space recovered locally (~13 MB from `.haiku/`).

**Negative:**
- Old git history still contains the heavy artifacts, so the `.git/` directory is unchanged in size. Aggressive cleanup would require `git filter-repo` — deferred.
- Two inline code refs (`graph.js:221`, `serve.sh:29`) had to change. A grep miss in either place would have left a dangling reference; verified via direct read of both lines before commit.

**Risks:**
- Anyone cloning a stale fork of `graph-rebuild` will get a 404 once the remote branch is deleted. Acceptable: branch was last advanced 06-17 and `main` carries every commit.
- Phase rebuilds now require ~15 min of local Ollama embed time on fresh clones (no cached `embeddings.npy`). Documented as expected behavior in `.scribe-skills/PLAN.md` already.

## Implementation Plan

- [x] `git mv docs/adr/0001-5*.md ADRs/decisions/`; `git mv` new two to `0006-` / `0007-`; `rmdir docs/adr`.
- [x] sed `ADR-0001 → ADR-0006` in `graph/graph.js`; `ADR-0002 → ADR-0007` in `serve.sh` and inside the renamed 0007 file's title.
- [x] Expand `ADRs/README.md` to the eight-row index table.
- [x] Append heavy-artifact patterns to `.gitignore`; `git rm --cached` matches.
- [x] Append `ollama pull qwen3-embedding:8b` to `setup.sh`; add row to SETUP.md.
- [x] `rm -rf .haiku/ knowledge/sources.json.bak .queue/__pycache__/`.
- [x] `git branch -d graph-rebuild`. Remote delete (`git push origin --delete graph-rebuild`) deferred — blocked by auto-mode classifier; user runs manually.
- [x] Verify post-restructure: `bash serve.sh` regenerates both graphs, server boots, browser opens, all ADR-NNNN refs point at existing files.

## Build Log

```
EVENT
problem: two ADR dirs with colliding numbers
solution: consolidate into ADRs/decisions/; renumber new ADRs by push date; sed two code refs
tests: ls ADRs/decisions/ shows 0001-0007; grep "ADR-0001\b" graph.js returns nothing
outcome: single canonical ADR location, code refs intact
```

```
EVENT
problem: ~100 MB of regenerable phase artifacts under version control
solution: .gitignore patterns + git rm --cached for embeddings.npy, candidate_pairs.json, batch_*.json
tests: git ls-files | wc -l drops by 100+; serve.sh still runs end-to-end
outcome: lighter clones; rebuild path intact
```

```
EVENT
problem: setup.sh missing qwen3-embedding:8b; fresh setups fail Phase 3b prepass
solution: append ollama pull line + SETUP.md row
tests: setup.sh idempotent; ollama list shows three models after fresh run
outcome: fresh clones can run v2 rebuild without manual pulls
```

```
EVENT
problem: stale graph-rebuild branch (0 ahead / 24 behind main)
solution: git branch -d graph-rebuild (local); remote delete deferred to user
tests: git branch -a shows only main; origin/graph-rebuild still present (manual cleanup pending)
outcome: local clean; remote pending
```

```
EVENT
problem: ADRs/README.md was a 7-line stub
solution: replace with a one-table index of all eight ADRs
tests: each table entry links to an existing file under decisions/
outcome: ADR directory now navigable from the README
```
