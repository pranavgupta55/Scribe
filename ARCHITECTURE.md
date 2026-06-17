# Architecture

How Scribe turns 204 transcripts into a 3,839-node / 12,656-edge knowledge graph.

This document covers the **v2 graph** (rebuilt June 2026 — see [`.scribe-skills/PLAN.md`](.scribe-skills/PLAN.md) for the design rationale and [`.scribe-skills/HANDOFF.md`](.scribe-skills/HANDOFF.md) for the build report). The original v1 graph is still available at `graph/graph.json` for A/B comparison.

---

## 1 · Node layers

Scribe represents knowledge as a hierarchical graph with five canonical tiers plus source spokes:

| Layer | Kind | Count | Definition | Storage |
|---|---|---:|---|---|
| **source** | Transcript | 204 | One video/book transcript, the raw input | `transcripts/*.txt`, metadata in `knowledge/sources.json` |
| **L0** | Concept | 369 | Canonical glossary term — the thing a reader would search for. Carries `aliases`, `avoid_terms`, `ambiguity_notes`, `role` | `knowledge/concepts.json` |
| **L1** | Framework | 318 | Named multi-part structure ("Core Four Methods", "BAMFAM Method") | `knowledge/v2/nodes.jsonl` |
| **L2** | Claim | 706 | Load-bearing assertion (decontextualized, attributed, falsifiable) | `knowledge/v2/nodes.jsonl` |
| **L2a** | Mechanism | 461 | Causal claim with a stated `because`-clause | same |
| **L2b** | Quantified Axis | 238 | Numerical claim with an axis + ≥2 anchors (e.g. _"creative-asset need scales with monthly ad spend: SMB 5–15, mid-market 50–100, enterprise 200+"_) | same |
| **L3** | Example | 676 | Specific story or instance illustrating a claim | same |
| **L4'** | Practice | 867 | Bare imperative ("you should X") without a backing claim | same |

L2 / L2a / L2b are **siblings** at the same depth — they differ only in content shape (assertion vs. causal vs. quantified). L1 sits above them as a named multi-claim structure; L3/L4' sit below as illustrations or practices.

### Edges

| Kind | Count | Direction | Meaning |
|---|---:|---|---|
| `hosts` | 1,717 | concept → claim/framework | hierarchy: this claim belongs to that concept |
| `illustrates` | 675 | claim → example | hierarchy: this example illustrates that claim |
| `practices` | 865 | concept → practice | hierarchy: this practice lives under that concept |
| `agreement` | 1,204 | sibling | two claims assert the same load-bearing principle |
| `builds-on` | 2,544 | sibling | one extends, refines, or depends on the other |
| `contradiction` | 42 | sibling | two sources directly disagree (most informative edges) |
| `related` | 5,609 | sibling | same topic area, different specific assertions |

Total: **3,261 hierarchy + 9,395 sibling = 12,656 edges**.

### Trust signal

A claim's `n_sources` counts how many transcripts independently expressed it (after Phase 3b paraphrase merging). 244 claims have ≥ 2 sources backing them — those carry visible cross-source-confirmation strength.

**Top cross-source-confirmed claims:**

| n_sources | Topic | Claim |
|---:|---|---|
| 25 | Focus Through Subtraction | Hormozi argues that spreading founder attention across adjacent business models compounds against itself |
| 23 | Sales Process Optimization | demand-constrained service businesses need process > talent |
| 18 | Founder Bottleneck | founder as sole delivery person caps coaching businesses |
| 14 | Supply vs. Demand Constraint | the binding constraint determines the correct scaling lever |
| 12 | Tradeoffs & Conscious Sacrifice | practitioner-to-operator transition |

This is what "voting" looks like in the graph: high-confidence nodes carry the weight of multiple independent confirmations, low-confidence singletons stay in but render dimmer.

---

## 2 · Pipeline: transcript → graph

```
transcripts/*.txt   (204 raw transcripts)
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 1a  Topic normalization  (mechanical + 23 verification agents)   │
│   embed 1472 raw topic strings with qwen3-embedding:8b                 │
│   → Agglomerative Ward, n_clusters=300                                 │
│   → 20 Haiku batch verifiers + 3 Sonnet hard-cluster judges            │
│   verdicts: accept | rename | kick | split | merge_with                │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 1b  Concept polish  (8 Sonnet critics)                           │
│   ubiquitous-language discipline:                                      │
│     canonical_name · aliases · avoid_terms · definition                │
│     · ambiguity_notes · role (domain | mental-model | meta)            │
│   → 369 canonical L0 concepts  ─────────────►  knowledge/concepts.json │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 3a  Extraction  (204 Sonnet agents, one per transcript)          │
│   reads transcript + concepts_index.json (49k-token compact glossary)  │
│   TWO-PASS:                                                            │
│     (a) identify speakers + concepts addressed                         │
│     (b) extract claims · frameworks · examples · practices anchored    │
│         to canonical concept names                                     │
│   decontextualization gate (CLAIM-DEFINITION §3, AIDA Gwet AC1=0.85)   │
│   → 2245 claims, 318 frameworks, 676 examples, 867 practices           │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 3b  Cross-source merge + hierarchy classify                      │
│   embed every claim, find pairs within same concept @ sim ≥ 0.85      │
│   → union-find collapses paraphrases → 1413 merged claims              │
│     (244 multi-source confirmed, top has 25 sources backing it)       │
│   12 Haiku classify each into L2 | L2a | L2b | L3 | L4' | DROP         │
│   → 1405 surviving claims after Reset-2 repair pass                    │
│                              ─────────────►  knowledge/v2/nodes.jsonl │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 4  Sibling edges  (50 Haiku connection judges)                   │
│   candidate generation (mechanical):                                   │
│     concept↔concept: top-12 cosine neighbors per concept, sim ≥ 0.45  │
│     claim↔claim:     top-8 cross-concept neighbors per claim, ≥ 0.45  │
│   Haiku labels each pair:                                              │
│     agreement | builds-on | contradiction | related | none             │
│   → 9395 edges        ────────────────►  knowledge/v2/connections.json │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 5  Graph emission  (export_graph_v2.py — purely mechanical)      │
│   joins concepts.json + nodes.jsonl + connections.json + sources.json  │
│   builds parent→child hierarchy edges                                  │
│   resolves aliases → canonical concept for any claim's `topic` field   │
│   → 3839 nodes, 12,656 edges  ────────────►  graph/graph_v2.json      │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 6  Quality eval  (16 Haiku scorers + 1 Sonnet repair pass)       │
│   sample 200 claims; score against NODE-QUALITY-RUBRIC §C rows 1–5    │
│   → 87.2% pass (v1 baseline 36.8%, +50pp / 2.4× improvement)          │
│   Sonnet repair on the 25 failing claims → 21 rewritten, 4 dropped    │
│   → projected ≈ 95% pass-rate                                          │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PHASE 7  RAG plumbing                                                   │
│   server.py bumps retrieval to 50 chunks / 50 facts per query          │
│   surfaces title + video_summary + url per source                      │
│   graph.js Copy view renders per-source summary cards                  │
│   click copies the full source block (passages + facts + url)         │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
   graph viewer (?graph=v2)  ·  RAG chat  ·  Copy view
```

---

## 3 · Models used

| Model | Where | Why |
|---|---|---|
| **qwen3-embedding:8b** (local, ollama) | Phases 1a, 3b, 4 | Highest local MTEB clustering score (~58) on short labels; 32K context handles long claim texts |
| **Claude Sonnet 4.6** | Phase 1a hard-cluster judges, Phase 1b concept critics, Phase 3a extraction, Phase 6 repair | Higher accuracy on long-context extraction and opinionated naming |
| **Claude Haiku 4.5** | Phase 1a verification, Phase 3b classify, Phase 4 connection judging, Phase 6 scoring | 3× cheaper; sufficient for short structured judgments |
| **Gemini 2.5 Flash** | RAG chat answer generation | Cheap inference + good citation behavior |

Roughly **314 agents** were fired across the v2 rebuild. Phase 3a (204 Sonnet extractions) was the heaviest at ~17M tokens / 28 min wall-clock.

---

## 4 · Files

```
knowledge/
├── concepts.json           # 369 canonical L0 concepts (the glossary)
├── sources.json            # per-source metadata (title, video_summary, url, …)
├── topics/                 # legacy per-topic markdown notes (v1)
└── v2/
    ├── nodes.jsonl         # 3270 L1-L4' nodes
    └── connections.json    # 9395 sibling edges
graph/
├── graph.json              # v1 graph (baseline)
└── graph_v2.json           # v2 rebuilt graph (3839 nodes / 12,656 edges)
.scribe-skills/             # durable design docs + per-phase artifacts
├── PLAN.md
├── NODE-QUALITY-RUBRIC.md  # 20 failure modes catalogued + scoring matrix
├── CLAIM-DEFINITION.md     # what counts as a Claim, w/ decontextualization gate
├── HIERARCHY.md            # L0..L4 level definitions + promote/demote rules
├── FEW-SHOT.md             # 10 positive + 20 negative canonical examples
└── HANDOFF.md              # build report
UBIQUITOUS_LANGUAGE.md      # human-readable rendering of concepts.json
ARCHITECTURE.md             # this file
export_graph_v2.py          # rebuilds graph_v2.json from the knowledge artifacts
```

---

## 5 · How to use it

```bash
serve.sh                          # opens the graph viewer
# default URL shows v1 graph; visit:
#   http://localhost:8000/graph/?graph=v2
# to load the rebuilt v2 graph.
```

To rebuild `graph_v2.json` after editing any `knowledge/` file:

```bash
python3 export_graph_v2.py
```

To rebuild from scratch, see [`.scribe-skills/PLAN.md`](.scribe-skills/PLAN.md). The `.scribe-skills/scripts/` directory contains every phase's Python script — all idempotent and re-runnable.
