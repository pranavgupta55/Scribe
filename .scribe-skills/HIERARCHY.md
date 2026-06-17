# Hierarchical Knowledge Structure

The previous graph was flat: every extracted concept became a topic node at the same level, with arbitrary string-matched edges between them. This made "Lead Generation" indistinguishable in shape from "Lead Getter Integration" — two nodes, two strings, no parent/child.

The new graph is hierarchical. A **Concept** sits at the top, sheltering one or more **Frameworks** or **Mental Models**, each of which is supported by **Claims**, **Mechanisms**, and **Quantified Axes**, each of which is illustrated by **Examples** and (when relevant) bounded by **Counterexamples** or referenced by **Metaphors**. Edges between concepts (sibling-edges, contradiction-edges) live alongside the parent-child edges.

This document defines the levels, what lives at each, how nodes promote/demote between levels, and how the hierarchy is rendered into `graph.json`.

## §1 — Levels (top-down)

### L0 — Concept
The umbrella term. The thing a reader would search for. The most-shared label across many sources. Example: **Lead Generation**, **Pricing Strategy**, **Hiring**, **Operational Definition of Learning**.

A concept has:
- a canonical **name** (one per concept)
- an **aliases** list (the topic strings from the original extraction that fold into it — "Lead Gen Channels", "Lead Generation Volume", "PPC Lead Generation", "Virtual Lead Gen" — see Phase 1 normalization in PLAN.md)
- a **one-paragraph definition** (what the concept *is*, written in the project's ubiquitous language)
- a **role** in the corpus: `domain` (a business / craft concept), `mental-model` (a way of thinking about a class of problems), `meta` (a concept about the corpus itself, e.g. "How to read a transcript")

A concept does not carry claims directly. It carries Frameworks and Claims as children.

### L1 — Framework / Mental Model
A named, structured way of organizing claims within (or across) concepts. Example: **Core Four Methods** (under Lead Generation), **Bullseye Framework** (under Customer Acquisition), **Operational Definition of Learning** (under Learning Theory).

A framework has:
- a **name** (often a multi-word proper noun)
- a **shape** (list of sub-parts, sequence of stages, matrix of axes, named axes — see template below)
- a **definition paragraph**
- one or more named **slots** which Claims fill

A framework is itself a node in the graph. Its `kind` is `framework`. Edges from claims to their framework are `kind: implements`.

### L1' — Metaphor / Analogy
A named comparison between two domains, declared as such. Example: **Mountain Climbing Metaphor** (for learning in increasing difficulty). Lives at L1 because it organizes a cluster of claims, but is rendered differently (the node title contains the word "Metaphor" or "Analogy" so a reader knows not to apply it literally).

### L2 — Claim
A single load-bearing assertion. See CLAIM-DEFINITION.md for the full shape.

Each claim sits under exactly one parent — either a Framework (L1), a Metaphor (L1'), or directly under a Concept (L0) when the claim does not fit inside any L1.

### L2a — Mechanism
A claim whose `type` is `mechanism` — i.e. it contains the causal because-clause. Same level as a regular claim; the distinction is type, not depth.

### L2b — Quantified Axis
A claim whose `type` is `quantified` — i.e. it carries the `numbers` block with multi-anchor axis. Same level as a regular claim.

### L3 — Example / Anecdote
A specific story or instance that illustrates a parent Claim. Plural per claim (often). Lives as a leaf.

### L3a — Counterexample
A case the speaker explicitly raises as bounding the parent claim. Lives as a leaf under the claim.

### L4 — Implementation Detail
A concrete how-to step or feature, leafmost. Lives under a Practice or under a Mechanism. Rarely surfaces in the visible graph — kept available for RAG.

### L4' — Practice
An imperative ("you should do X") without a backing Claim. Lives in a `practices[]` array per Concept. Searchable but not graph-rendered by default (toggleable in UI).

## §2 — How nodes promote / demote

The extraction pass produces candidate nodes at multiple levels. The verification pass decides which level each candidate belongs to. The rules:

- A candidate **claim** that fails Section C of NODE-QUALITY-RUBRIC → either rewritten to pass, demoted to **example** under a real claim, or dropped.
- A candidate **claim** that turns out to be a paraphrase of an existing claim on the same concept → folded into the existing claim's `attribution` list as a second source (drives cross-source agreement weight).
- A candidate **example** that is asserted as universally true rather than illustrative → promoted to claim only if it can carry conditions + mechanism; otherwise demoted to practice.
- A candidate **framework** that has only one child claim → either demoted (the framework name becomes an alias of the child claim's topic) or merged into another framework.
- A candidate **concept** that has only one framework and few claims → either folded into a parent concept as an alias, or kept if it is genuinely orthogonal (a niche concept can still survive at L0 if its definition is clean).

## §3 — Edges in the hierarchy

Parent-child edges (hierarchy):
- `concept → framework`: `kind: hosts`, weight 1.0
- `concept → claim`: `kind: hosts`, weight 1.0 (only if claim has no L1 parent)
- `framework → claim`: `kind: implements`, weight 1.0
- `claim → example`: `kind: illustrates`, weight 0.8
- `claim → counterexample`: `kind: bounds`, weight 0.8
- `claim → implementation_detail`: `kind: details`, weight 0.5

Sibling edges (cross-cutting):
- `claim ↔ claim`: `kind: agreement | builds-on | contradiction | related`, weight per CONN_PROMPT v2
- `concept ↔ concept`: `kind: related | umbrella-of | contains`, weight from co-occurrence + Pass D
- `framework ↔ framework`: `kind: alternative-to | builds-on | contradiction`

The graph viewer (graph/index.html + graph.js) reads `parent_id` on each node for the hierarchical layout (D3 hierarchical force or radial tree per concept) and the existing `links` for the cross-cutting edges. Source nodes (video files) remain at their own layer, edged to concepts they appear in, not to individual claims.

## §4 — Source spokes (how transcripts attach)

A **source** (video transcript) is not in the L0-L4 hierarchy. It is an orthogonal anchor.

Each source carries:
- a `video_summary` (already present)
- a list of `concept_ids[]` it appears in
- per-concept: a list of `claim_ids[]` it asserted, with transcript offsets

This is how cross-source agreement and contradiction are tracked: claim X is asserted by sources A, B, C → high confidence; claim X contradicts claim Y, claim X has sources {A,B}, claim Y has sources {C,D} → renders as a contradiction edge with provenance on both sides.

## §5 — How the existing data maps in

The current `knowledge/sources.json` and `knowledge/topics/*.md` already contain raw material for L0-L3:

- The `topics[]` list per source → L0 concept candidates (after Phase 1 normalization)
- The `claims[]` list per source → L2 claim candidates (after Phase 3 quality pass)
- The `notes[topic].sections[]` list → L1 framework candidates (when a topic's notes contain a "Framework" section) and L3 example candidates (when a section is "Examples")
- The `notes[topic].headline` → L0 concept's one-paragraph definition (after rewrite to canonical form)

The migration is mechanical for the slot mapping and LLM-driven for the rewrites.

## §6 — Counts (sanity check)

Target ranges after the rebuild:

| Level | Current | Target | Notes |
|---|---|---|---|
| L0 Concept | 1472 (over-fragmented) | 200-350 | Phase 1 normalization |
| L1 Framework | ~0 (implicit) | 80-200 | Extracted in Phase 3 from `notes[].sections[]` of type Framework |
| L1' Metaphor | ~5 (accidental) | 20-50 | Extracted explicitly in Phase 3 |
| L2 Claim | ~5000 (`claims[]` across sources) | 4000-7000 | Many merged via cross-source agreement |
| L3 Example | inline with claims | 3000-5000 | Demoted from over-specific "claims" |
| L4' Practice | inline with claims | 1000-2000 | Demoted from imperative "claims" |

Source nodes stay at 204 (one per processed transcript).
