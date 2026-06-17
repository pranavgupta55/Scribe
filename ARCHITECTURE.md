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

Shapes have meaning:
- **Cylinder** = persistent file on disk
- **Rectangle** = deterministic Python (no LLM)
- **Parallelogram** = embedding step (local ollama model)
- **Hexagon** = LLM agent invocation (model + count + prompt summary inside)
- **Diamond** = decision / branch
- **Stadium** = user-facing endpoint

```mermaid
flowchart TD
    classDef file fill:#0d1117,stroke:#58a6ff,color:#c9d1d9,stroke-width:2px
    classDef code fill:#161b22,stroke:#7d8590,color:#c9d1d9
    classDef embed fill:#0e2218,stroke:#56d364,color:#c9d1d9,stroke-width:2px
    classDef llm fill:#221610,stroke:#f78166,color:#c9d1d9,stroke-width:2px
    classDef decision fill:#22200c,stroke:#f1e05a,color:#c9d1d9,stroke-width:2px
    classDef endpoint fill:#1a1726,stroke:#bc8cff,color:#c9d1d9,stroke-width:3px

    USER([user runs scribe.sh + updateDB.sh]):::endpoint
    TXT[("transcripts/*.txt<br/>~200 files")]:::file
    SRC[("knowledge/sources.json<br/>raw topic strings + metadata")]:::file
    USER --> TXT
    USER --> SRC

    %% ═══════════════════════════════ Phase 1a ═══════════════════════════════
    subgraph P1A["Phase 1a · normalize ~1.5k raw topic strings → ~300 clusters"]
        direction TB
        P1A_in[/"~1.5k raw topic strings"/]:::code
        P1A_emb[/"qwen3-embedding:8b (local)<br/>input: 'name + headline'<br/>output: 4096-d vector per topic"/]:::embed
        P1A_clu["Ward agglomerative (sklearn)<br/>L2-normalized · n_clusters=300<br/>+ second cut at n=50 for super-concepts"]:::code
        P1A_rank["rank clusters by intra-cluster variance<br/>top-12 hardest split off for Sonnet"]:::code
        P1A_hai{{"20 Haiku verifiers · ~15 clusters each<br/>system prompt: ubiquitous-language §rules +<br/>RUBRIC §B positive signals + FEW-SHOT positives<br/>output: verdict per cluster"}}:::llm
        P1A_son{{"3 Sonnet judges · 4 hard clusters each<br/>extra instruction: 'be ruthless · default to split'"}}:::llm
        P1A_dec{verdict?}:::decision
        P1A_kick[("review queue<br/>~70 kicked 'orphan' topics")]:::file
        P1A_draft[("~380 draft concepts<br/>(after splits/merges/kicks applied)")]:::file

        P1A_in --> P1A_emb --> P1A_clu --> P1A_rank
        P1A_rank -->|288 easy| P1A_hai
        P1A_rank -->|12 hard| P1A_son
        P1A_hai --> P1A_dec
        P1A_son --> P1A_dec
        P1A_dec -->|accept · rename · split · merge_with| P1A_draft
        P1A_dec -->|kick a member| P1A_kick
    end
    SRC --> P1A_in

    %% ═══════════════════════════════ Phase 1b ═══════════════════════════════
    subgraph P1B["Phase 1b · polish concepts (ubiquitous-language discipline)"]
        direction TB
        P1B_in[/"~380 draft concepts + ~70 orphans<br/>split into 8 batches"/]:::code
        P1B_son{{"8 Sonnet critics in parallel<br/>system prompt: ubiquitous-language.md verbatim +<br/>CONTEXT-FORMAT.md · 'be opinionated'<br/>per concept: refine canonical_name, build aliases[],<br/>list avoid_terms[], 1-line definition,<br/>flag ambiguity_notes, role ∈ {domain · mental-model · meta}<br/>also: assign each orphan, propose in-batch merges"}}:::llm
        P1B_agg["aggregate · apply ~15 merges ·<br/>resolve orphan assignments"]:::code
        CONCEPTS[("knowledge/concepts.json<br/>~370 canonical L0 Concepts<br/>300 domain · 68 mental-model · 1 meta")]:::file

        P1B_in --> P1B_son --> P1B_agg --> CONCEPTS
    end
    P1A_draft --> P1B_in
    P1A_kick --> P1B_in

    %% ═══════════════════════════════ Phase 3a ═══════════════════════════════
    subgraph P3A["Phase 3a · extract claims (204 Sonnet agents · one per transcript)"]
        direction TB
        P3A_index["build concepts_index.json<br/>(compact ~50KB glossary subset)"]:::code
        P3A_dispatch["Workflow tool · concurrency cap 16<br/>fan-out 204 Sonnet agents"]:::code
        P3A_agent{{"Sonnet 4.6 extractor (×204)<br/>reads: one transcript + concepts_index +<br/>NODE-QUALITY-RUBRIC §A + CLAIM-DEFINITION §3 +<br/>FEW-SHOT.md + HIERARCHY.md<br/>system prompt: decontextualization gate ·<br/>'never invent concepts; anchor topic to canonical_name'<br/>PASS 1: identify speakers + concepts addressed<br/>PASS 2: extract claims · frameworks · examples · practices"}}:::llm
        P3A_gleam{"YES/NO gate ·<br/>'more entities missed?'"}:::decision
        P3A_loop{{"gleaning pass (conditional)<br/>same agent, prompted to add missed entities only"}}:::llm
        EXTRACT[(".scribe-skills/phase3a/extracted/{name}.json<br/>204 files · ~2.2k claims · ~320 frameworks ·<br/>~680 examples · ~870 practices")]:::file

        P3A_index --> P3A_dispatch --> P3A_agent --> P3A_gleam
        P3A_gleam -->|YES| P3A_loop --> EXTRACT
        P3A_gleam -->|NO| EXTRACT
    end
    CONCEPTS --> P3A_index
    TXT --> P3A_agent

    %% ═══════════════════════════════ Phase 3b ═══════════════════════════════
    subgraph P3B["Phase 3b · cross-source merge + hierarchy classify"]
        direction TB
        P3B_pool["aggregate all 204 extractions into one pool"]:::code
        P3B_emb[/"qwen3-embedding:8b<br/>input: each claim's text<br/>output: ~2.2k × 4096-d vectors"/]:::embed
        P3B_pairs["within each canonical topic, find pairs<br/>with cosine sim ≥ 0.85"]:::code
        P3B_uf["union-find collapse paraphrases<br/>~2.2k claims → ~1.4k merged groups<br/>centroid text = canonical · members → attribution_list"]:::code
        P3B_split["split into 12 batches × ~120 claims"]:::code
        P3B_class{{"12 Haiku classifiers in parallel<br/>system prompt: HIERARCHY.md §1 + §2 promote/demote<br/>verdict per claim:<br/>L2 (claim) · L2a (mechanism) · L2b (quantified axis) ·<br/>L3 (example) · L4' (practice) · DROP"}}:::llm
        NODES[("knowledge/v2/nodes.jsonl<br/>~3.3k nodes total:<br/>~1.4k claims · ~320 frameworks ·<br/>~680 examples · ~870 practices")]:::file

        P3B_pool --> P3B_emb --> P3B_pairs --> P3B_uf --> P3B_split --> P3B_class --> NODES
    end
    EXTRACT --> P3B_pool

    %% ═══════════════════════════════ Phase 4 ═══════════════════════════════
    subgraph P4["Phase 4 · sibling edges (50 Haiku connection judges · no NLI pre-filter)"]
        direction TB
        P4_cemb[/"qwen3-embedding:8b<br/>input: 'concept_name + definition'<br/>output: 370 × 4096-d"/]:::embed
        P4_cc["candidate concept↔concept pairs<br/>top-12 nearest per concept · sim ≥ 0.45<br/>= ~1.3k pairs"]:::code
        P4_cl["candidate claim↔claim pairs (cross-concept only)<br/>top-8 nearest per claim · sim ≥ 0.45<br/>drop same-source pairs<br/>= ~13k pairs"]:::code
        P4_split["mix concept + claim pairs ·<br/>trim text to 200 chars · split into 50 batches × ~290"]:::code
        P4_hai{{"50 Haiku connection judges in parallel<br/>system prompt: CLAIM-DEFINITION §4 voting model +<br/>RUBRIC §A11 (attribution) + §A18 (vague comparatives)<br/>verdict per pair:<br/>agreement · builds-on · contradiction · related · none<br/>+ confidence ∈ [0,1] + substantive sentence"}}:::llm
        P4_keep{conf ≥ 0.5?}:::decision
        CONN[("knowledge/v2/connections.json<br/>~9.5k surviving edges:<br/>5.6k related · 2.5k builds-on ·<br/>1.2k agreement · 42 contradiction")]:::file
        P4_drop["~5k 'none' verdicts discarded"]:::code

        P4_cemb --> P4_cc
        P4_cc --> P4_split
        P4_cl --> P4_split
        P4_split --> P4_hai --> P4_keep
        P4_keep -->|yes · emit| CONN
        P4_keep -->|no| P4_drop
    end
    CONCEPTS --> P4_cemb
    NODES --> P4_cl

    %% ═══════════════════════════════ Phase 5 ═══════════════════════════════
    subgraph P5["Phase 5 · graph emission (export_graph_v2.py · pure code)"]
        direction TB
        P5_load["load concepts + nodes.jsonl + connections + sources.json"]:::code
        P5_alias["build alias lookup table:<br/>every canonical_name and alias (lower-cased) →<br/>concept_id"]:::code
        P5_hier["mechanically add hierarchy edges:<br/>concept --hosts--> claim or framework<br/>claim --illustrates--> example<br/>concept --practices--> practice"]:::code
        P5_sib["attach sibling edges from connections.json<br/>resolve claim by position index → node_id"]:::code
        GRAPH[("graph/graph_v2.json<br/>~3.8k nodes · ~12.7k edges<br/>hierarchy + sibling + source spokes")]:::file

        P5_load --> P5_alias --> P5_hier --> P5_sib --> GRAPH
    end
    CONCEPTS --> P5_load
    NODES --> P5_load
    CONN --> P5_load
    SRC --> P5_load

    %% ═══════════════════════════════ Phase 6 ═══════════════════════════════
    subgraph P6["Phase 6 · quality eval + repair loop"]
        direction TB
        P6_samp["stratified sample 200 claims<br/>(80 multi-source + 120 single-source)"]:::code
        P6_split["split into 16 batches × ~12-13 claims"]:::code
        P6_hai{{"16 Haiku scorers in parallel<br/>system prompt: NODE-QUALITY-RUBRIC §C rows 1-5<br/>+ §A failure modes (A1..A20)<br/>verdict per claim: each row binary · pass = all 5 true"}}:::llm
        P6_decide{pass-rate ≥ 80%?}:::decision
        P6_repair{{"1 Sonnet repair pass over the failing claims<br/>system prompt: §A failure modes + §3 claim shape<br/>action per claim: rewrite (fix conditions/mechanism/<br/>numbers) OR drop if unrescuable"}}:::llm
        P6_apply["apply rewrites · drop irrecoverable<br/>nodes.jsonl: ~1409 → ~1405 claims"]:::code
        P6_done([eval passed · ship · 87% → ~95% post-repair]):::endpoint

        P6_samp --> P6_split --> P6_hai --> P6_decide
        P6_decide -->|yes, ≥80%| P6_repair
        P6_decide -->|no| P6_repair
        P6_repair --> P6_apply --> P6_done
    end
    NODES --> P6_samp
    P6_apply -. update nodes .-> NODES
    P6_apply -. trigger re-emit .-> P5_load

    %% ═══════════════════════════════ Phase 7 ═══════════════════════════════
    subgraph P7["Phase 7 · RAG plumbing (server.py + graph.js)"]
        direction TB
        CHROMA[(".chroma/<br/>chunks + facts collections<br/>nomic-embed-text 768-d (v1, legacy)")]:::file
        P7_q["user query · embed via nomic-embed-text"]:::code
        P7_retrieve["retrieve_structured()<br/>top-50 chunks + top-50 facts (was 25 + 30)"]:::code
        P7_attach["attach title + video_summary + url<br/>per source from sources.json"]:::code
        P7_chat([RAG chat answer<br/>(Gemini 2.5 Flash)]):::endpoint
        P7_copy([Copy-view source cards<br/>summary visible · click copies full block]):::endpoint

        P7_q --> CHROMA
        CHROMA --> P7_retrieve --> P7_attach
        P7_attach --> P7_chat
        P7_attach --> P7_copy
    end
    SRC --> P7_attach

    %% ═════════════════════ user-facing endpoints ═════════════════════
    GRAPH --> VIEW([graph viewer<br/>http://localhost:8000/graph/?graph=v2]):::endpoint
    GRAPH -. optional Phase 8 .-> P7_retrieve
```

### Recursive loops surfaced by the diagram

- **Phase 3a gleaning** — each Sonnet extractor checks a YES/NO gate ("did I miss entities?") and, if YES, runs a second pass on the same transcript focused only on recovering missed entities. GraphRAG-style efficiency: one conditional re-prompt per source instead of a full plan→draft→verify cycle.
- **Phase 6 repair feedback** — failing claims feed back into `nodes.jsonl` after Sonnet rewrites, which re-triggers Phase 5 graph emission. The eval cutoff (≥80%) gates whether repair is even needed; in practice Reset-2 hit 87% raw and we ran the Sonnet repair anyway to lift the long-tail failures into the rebuilt graph.
- **Phase 1a verdict branching** — the verifier can `kick` individual members from an otherwise-good cluster; kicked members loop forward into Phase 1b's orphan-assignment step rather than dropping. No member is lost without an explicit `DROP` verdict somewhere.

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
