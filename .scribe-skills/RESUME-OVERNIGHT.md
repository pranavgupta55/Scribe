# Overnight Resume Plan (2026-06-17)

State at pause: **23:44 PDT 2026-06-16, user at 85% account usage.**

## Scheduled wake-ups (in-memory session crons)

| Job ID     | Fires at     | Purpose                                       |
| ---------- | ------------ | --------------------------------------------- |
| ee0c99e7   | 02:18 PDT    | Reset 1 — resume Phase 3a + advance further   |
| cc5bc233   | 07:18 PDT    | Reset 2 — finish Phase 3b…7 by 08:45 PDT      |

User leaves for work at ~09:00. All work must commit-locally by 08:45. Do NOT push to GitHub overnight.

## What completed before the pause

- **Phase 0 (research + diagnostic):** all 27 agents done. See `.scribe-skills/research/`.
- **Phase 1a (Ward cluster + 23 verification agents):** 1472 → 383 concepts. See `.scribe-skills/phase1a/`.
- **Phase 1b (8 Sonnet concept-naming critics):** 383 → 369 final L0 Concepts. See `knowledge/concepts.json`.
- **Phase 3a (extraction):** 26 of 204 transcripts done. See `.scribe-skills/phase3a/extracted/`. Workflow runId `wf_b60924e0-4e7`.

## Manual resume (if both crons fail to fire)

If you wake up and find Phase 3a never resumed:

```bash
cd "/Users/pranavgupta/VSCode Projects/Scribe"
# Confirm current state
ls .scribe-skills/phase3a/extracted/ | wc -l   # should be 26 if no resume happened
# Manual resume via Claude Code:
#   Run a new Claude Code session and ask: "Resume the Phase 3a Workflow with runId wf_b60924e0-4e7"
```

## Subsequent phases (in order)

3. **Phase 3a** complete (204 extraction files in `.scribe-skills/phase3a/extracted/`).
4. **Phase 3b** — hierarchy classify + cross-source claim merge. Output: `knowledge/v2/nodes.jsonl`.
5. **Phase 2** — re-embed claim texts via qwen3-embedding:8b with title+premise prepend.
6. **Phase 4** — connection rebuild v2 (~40 Haiku agents, no NLI pre-filter). Output: `knowledge/v2/connections.json`.
7. **Phase 5** — `export_graph_v2.py` + viewer drilldown. Output: `graph/graph_v2.json`.
8. **Phase 6** — quality eval (16 Haiku + 4 Sonnet auditors). Cutoff: ≥80% claim-pass-rate on §C scoring matrix and ≥25% improvement over v1 baseline.
9. **Phase 7** — `server.py` RAG update for the new L0..L4 hierarchy + bump `RAG_N_FACTS=50`, `RAG_N_CHUNKS=50` + Copy view video_summary cards per the earlier request.

## Important constraints carried forward

- **Drop the NLI pre-filter** in Phase 4 per user §−2 directive (use Haiku directly on all candidate pairs).
- **Use Sonnet only for** Phase 6 (4 auditors) and any hard-cluster judgments. Bulk extraction/classification = Haiku.
- **Decontextualization** is the primary positive gate (CLAIM-DEFINITION §3, AIDA Gwet AC1=0.85). A claim that fails decontextualization is rejected before any rubric row is checked.
- **`speaker_term` is REQUIRED** in every claim (may be null only when the speaker used the canonical name verbatim).
- **Stop hard at usage ≥80%** in either reset to leave headroom.
- **No GitHub push** until user authorizes. Local commits only.

## If something goes very wrong

Leave a `.scribe-skills/HANDOFF.md` at the end of each reset window summarizing:

- What was done in this window
- What remains
- Any unexpected failures or blocked steps
- Estimated work remaining

The user will read it on return at ~09:30.
