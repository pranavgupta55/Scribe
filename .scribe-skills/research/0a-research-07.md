# 0a-Research-07 — Ubiquitous Language Outside Software: Terminology Consistency in Knowledge Bases, Encyclopedias, PKM Tools, and NLP Pipelines

**Question:** How are DDD-style "ubiquitous language" disciplines applied outside software — in knowledge bases, encyclopedias (Wikipedia's MOS), research notes (Zettelkasten), corporate wikis, and AI-assisted note-taking tools (Obsidian, Logseq, Tana)? What problems do they hit that Scribe will hit too — extracting a consistent glossary from third-party source material (video transcripts) rather than authoring it on a team? How do they handle (a) speaker-specific terminology, (b) the same term meaning different things in different sources, (c) drift over time as new sources arrive?

---

## 1. The Common Problem Across All Domains

DDD's ubiquitous language has a critical pre-condition that all non-software knowledge systems lack: **a bounded team that can negotiate and converge on terms**. In DDD, if "order" and "purchase" collide, you call a meeting. In every external-source knowledge system — Wikipedia, Zettelkasten, corporate wikis, Scribe — there is no meeting. Terminology arrives pre-formed from sources you do not control.

This distinction is the root cause of every problem catalogued below.

---

## 2. Andy Matuschak's Evergreen Notes: Concept-Oriented Naming as Implicit Canonicalization

Matuschak's system requires notes to be **concept-oriented** rather than source-oriented. The key instruction: organize by concept rather than by author or book, because "if you just organize your notes around books, you just have a scattered set of notes on the concept, perhaps referring to it by different names, each embedded in some larger document."

Critically, Matuschak wraps this in the idea of **concept handles** (via Scott Alexander): a concept handle is a memorable noun phrase that crystallizes an amorphous idea so communities can discuss and apply it. Handles function as "APIs for concepts" — the note title is the API surface; every reference to that note uses that title.

**What this solves:** When you own the notes (first-person PKM), you pick the canonical handle and file every source's usage under it.

**What this does NOT solve:** The article on `Evergreen_notes_should_be_concept-oriented` explicitly acknowledges the fragmentation problem but provides no algorithmic solution for resolving it. It only states the desirable outcome. The mechanism for deciding "this author's 'conversion rate' and that author's 'close ratio' should merge under one handle" is left entirely to human judgment.

**Sources:**
- [Evergreen notes should be concept-oriented](https://notes.andymatuschak.org/Evergreen_notes_should_be_concept-oriented)
- [Concept handles, after Alexander](https://notes.andymatuschak.org/z3b7sidNrEkNaY9qfGwZjwz)

---

## 3. Maggie Appleton: Tools-for-Thought Terminology Inflation

Appleton identifies a related pathology at the community level. In her essay "Tools for Thought as Cultural Practices, not Computational Objects," she documents that the phrase "tools for thought" has become so overloaded — appearing across PKM, note-taking, CSCW, HCI, knowledge graphs, VC databases — that it obscures more than it reveals. She traces this to the **absence of an agreed naming authority**: different communities (researchers, product designers, VC firms, indie hackers) each reframe a cluster of ideas under their preferred term.

Her proposed fix — renaming the field "CMFT" (computational mediums for thought) or even "CMFWCKW" to expose its actual demographic scope — is an exercise in canonical naming from first principles. But she's doing it as an external observer, which is exactly Scribe's position: an outside agent trying to impose consistent vocabulary on sources that predate the canonicalization effort.

**The Scribe parallel:** Scribe is doing what Appleton is trying to do for TfT, but at claim-extraction scale: one canonical label per concept, aliases captured, competing framings surfaced as contradictions or cross-source agreements rather than silently conflated.

**Source:** [Tools for Thought as Cultural Practices, not Computational Objects](https://maggieappleton.com/tools-for-thought)

---

## 4. Digital Gardens (Appleton's Garden History): Loose Consistency as a Feature

In "A Brief History & Ethos of the Digital Garden," Appleton documents that digital gardens deliberately accept terminological looseness as a design choice. Gardens use **bi-directional links** and **epistemic status markers** instead of enforced vocabulary. The philosophy is: "gardens are imperfect by design" — meaning accumulates through contextual association, not through canonical naming.

This is the **anti-pattern** Scribe must avoid. A digital garden of video transcripts would give you 197 loosely-linked note clusters where "lead" (Hormozi) and "prospect" (another speaker) and "potential customer" (a third) co-exist without being resolved. The graph would split what should be one node into three.

**Source:** [A Brief History & Ethos of the Digital Garden](https://maggieappleton.com/garden-history)

---

## 5. Wikipedia's Manual of Style: The Institutional Canonicalization Apparatus

Wikipedia represents the most mature external-source terminology discipline outside software. Its solutions are worth studying in detail because Wikipedia, like Scribe, extracts information from sources it did not author.

### 5a. Primary Topic and Hatnotes

When the same word has multiple meanings, Wikipedia's [Disambiguation policy](https://en.wikipedia.org/wiki/Wikipedia:Disambiguation) establishes:
1. Identify a **primary topic** (the meaning most readers intend).
2. That primary topic holds the unqualified title.
3. All competing meanings live at `Term (qualifier)` pages.
4. Every use of the term carries a **hatnote** pointing to the disambiguation page.

This is operationally equivalent to: pick a canonical term, document all aliases, surface the disambiguation explicitly rather than burying it.

**Scribe equivalent:** Canonical topic node with `aliases[]` field; claims from sources that use non-canonical names map to the canonical node; a `contradictions.json` edge flags when two sources use the same word to mean genuinely different things (rather than just different surface forms of the same concept).

### 5b. Internal Consistency Requirement

Wikipedia's [WP:CONSISTENCY](https://en.wikipedia.org/wiki/Wikipedia:Consistency) requires consistent terminology **within** an article, enforced by editors. Cross-article standardization is governed by the principle that "titles for the same kind of subject should not differ in form or structure without good reason."

**What Wikipedia cannot do that Scribe must:** Wikipedia's consistency is human-maintained per article. Scribe needs this enforced **across 197+ source files automatically**, which no manual editorial process can achieve at that scale.

### 5c. Word-Sense Disambiguation (WSD) Within Articles

Wikipedia's [MOS:WORDS](https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Words_to_watch) maintains a "words to watch" list — terms that are inherently loaded, ambiguous, or non-neutral. Each flagged term has a canonical alternative.

**Scribe's equivalent:** CLAIM-DEFINITION.md §6 ambiguity table. The `"fact" → Claim`, `"insight" → Claim or Mechanism`, `"principle" → Framework or Mechanism` mappings are Scribe's "words to watch" list.

**Sources:**
- [Wikipedia:Disambiguation](https://en.wikipedia.org/wiki/Wikipedia:Disambiguation)
- [Wikipedia:Consistency](https://en.wikipedia.org/wiki/Wikipedia:Consistency)
- [Wikipedia:Manual of Style/Words to watch](https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Words_to_watch)

---

## 6. Zettelkasten: The Alias Problem in Practice

The Zettelkasten method's note-typing taxonomy suffers from the exact drift Scribe will face. The [Zettelkasten.de](https://zettelkasten.de/posts/kinds-of-ties/) literature documents five note-connection mechanisms (tags, links, folgezettel, juxtaposition, categories) — none of which explicitly handle **synonym resolution**. The method literature acknowledges that "the most important type of note doesn't have a name" and that terminology around note types is "unstandardized across practitioners."

Practically, Zettelkasten handles the same-concept/different-names problem through:
- **Tags** (which create overlap clusters, but do not enforce canonical naming)
- **Structure notes** (MOC/Outline notes that gather dispersed notes on one concept)
- **Human judgment** at filing time

The [Zettelkasten forum](https://forum.zettelkasten.de/discussion/1729/) identifies the downstream symptom: "a problem when multiple outlines have many of the same notes" — the same node ends up filed under multiple topic branches because its name changes between contexts.

**The Scribe parallel:** Pass D (cross-source connections) must do programmatically what Zettelkasten practitioners do manually: decide that note A and note B, despite different titles, represent the same claim.

**Source:** [Zettelkasten.de — Kinds of Ties Between Notes](https://zettelkasten.de/posts/kinds-of-ties/)

---

## 7. PKM Tools (Obsidian, Logseq, Tana, Roam): Alias Management in Practice

### 7a. Obsidian

Obsidian's `aliases:` frontmatter property is the primary mechanism. A note titled "Lifetime Customer Value" can alias `LTV`, `CLTV`, `customer lifetime value`. Incoming links to any alias resolve to the canonical note. The [Virtual Linker plugin](https://obsidianstats.com/plugins/virtual-linker) automatically highlights unlinked alias occurrences in other notes.

**The failure mode:** Aliases require human curation. When notes are generated from third-party transcripts (rather than authored by the vault owner), no one has reviewed the transcripts to decide that Hormozi's "LTV" and another speaker's "lifetime value of a customer" should alias. The alias list starts empty and fills only as contradictions become visible.

Several community plugins ([obsidian-glossary](https://github.com/felpsey/obsidian-glossary), [obsidian-auto-glossary](https://github.com/ennioitaliano/obsidian-auto-glossary)) allow building a glossary index from notes, but none automatically detect that two differently-named notes are semantically equivalent.

### 7b. Logseq

Logseq's `alias::` property works similarly but has a documented fragmentation bug: [when you mark an existing page as an alias for another, their contents remain separated in two pages](https://github.com/logseq/logseq/issues/4342). The merge is nominal — the underlying data is not actually unified. This forces users to manually copy content across, which is impractical at Scribe's scale.

### 7c. Tana

Tana's **supertags** represent the most schema-enforced approach: when you tag a node `#claim`, it automatically inherits predefined fields (speaker, source, conditions, etc.). This is structurally equivalent to Scribe's claim schema, and it enforces field consistency. However, Tana's supertags enforce *structure*, not *vocabulary* — two claims can be structurally identical but use "conversion rate" and "close rate" as their concept label without Tana detecting the collision.

### 7d. Roam Research

Roam [GitHub issue #67](https://github.com/Roam-Research/issues/issues/67) documents the canonical alias request: "Create alias and maintain references — the page would be known under 2 names and page references to either would stay the same." As of the issue filing, this was not natively supported; users relied on workarounds. This is a fundamental gap: an alias system that doesn't propagate references is useless for retroactive canonicalization of imported transcripts.

**The core finding across all PKM tools:** Every tool provides alias/synonym support for *manually* curated content. None provides automatic detection that two differently-named entries in third-party source material refer to the same concept. That detection problem is Scribe's Pass D.

**Sources:**
- [The Importance of Aliases (Gödel's)](https://www.goedel.io/p/the-importance-of-aliases)
- [Logseq alias issue #4342](https://github.com/logseq/logseq/issues/4342)
- [Roam alias issue #67](https://github.com/Roam-Research/issues/issues/67)
- [Tana supertags overview](https://medium.com/intelliboosters/the-power-of-supertags-in-tana-revolutionizing-personal-knowledge-management-69245022e7f0)

---

## 8. Corporate Wikis: The Institutional Drift Failure Mode

Corporate knowledge bases suffer from a well-documented pattern: "a Noah's Ark of styles, layouts, and terminology when different professionals contribute." Different departments use "client" / "account" / "customer" / "user" interchangeably; without governance (ownership + approval workflows), the wiki accumulates terminological debris over time.

The ontology research community formalizes this as **ontology drift**: concept meaning in a TBox shifts through versioning, iterations, or reinterpretation by different user communities. The SemaDrift and OntoDrift frameworks measure this using *morphing-chain approaches* (compare concept X in version N vs. N+1) and *identity-based approaches* (assume a stable pairing between concept versions and track divergence).

**The Scribe-specific form of drift:**
- Scribe's sources arrive incrementally (currently 98/197 processed, ~99 pending).
- Each new batch may introduce speakers who use existing terms in new ways or coin new terms for existing concepts.
- A glossary extracted after batch 1 will not automatically update when batch 2 contradicts it.
- This is ontology drift from the ingestion side, not the editing side.

**Sources:**
- [Ontology drift is a challenge for explainable data governance (arXiv 2108.05401)](https://arxiv.org/pdf/2108.05401)
- [Do you catch my drift? — ACM DL](https://dl.acm.org/doi/fullHtml/10.1145/3587259.3627555)
- [Knowledge graph embeddings for dealing with concept drift — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1570826820300585)

---

## 9. BookNLP: Coreference in Long-Form Narrative

[BookNLP](https://github.com/booknlp/booknlp) is the most relevant NLP pipeline for Scribe's problem because it operates on book-length documents — the same scale as a corpus of video transcripts — and must resolve that "Tom", "Tom Sawyer", and "Mr. Sawyer" are all the same entity.

BookNLP's approach:
1. **Character name clustering** first: group all surface variants of a named entity before any pronoun resolution.
2. **Constrained coreference second**: pronouns can co-refer to named or common entities, but common entities ("the boy") cannot co-refer to named entities ("Tom") by default. This prevents erroneous merging of distinct characters.
3. **Full coreference is opt-in** (`pronominalCorefOnly=False`) with an explicit warning: "be sure to inspect the output!" — indicating it remains error-prone.

**The lesson for Scribe:** BookNLP's two-stage design (cluster names first, then resolve pronouns) maps onto Scribe's needs: first build a canonical alias cluster for each concept (all surface forms that refer to "LTV"), then resolve claim references to the canonical node. Attempting full coreference across all nominal references in one pass produces unacceptable false-positive merges.

**Source:** [BookNLP GitHub](https://github.com/booknlp/booknlp)

---

## 10. SciCo: Hierarchical Cross-Document Coreference for Scientific Concepts

[SciCo (Cattan et al., 2021)](https://arxiv.org/abs/2104.08809) is the closest academic precedent to Scribe's problem. SciCo addresses identifying when different scientific papers refer to the same concept under different names ("class imbalance" = "skewed label distribution") AND detecting hierarchical relationships between concepts ("network embedding" is a child of "representation learning").

SciCo's **H-CDCR** task jointly:
- Clusters co-referring concept mentions across documents
- Infers a hierarchy between those clusters

The 2024 follow-on work ([arXiv 2409.15113](https://arxiv.org/html/2409.15113v1)) added **definition-augmented relational reasoning**:
1. **Singleton definitions** via RAG: retrieve literature-based definitions for each concept mention in context.
2. **Relational definitions**: generate a definition of *how two specific concepts relate* (not just what each is), using Mixtral 8x7B.
3. A two-stage re-ranking pipeline to avoid O(n²) definition generation.

**The four pairwise relations the system classifies:** coreference, parent-child hierarchy, inverse hierarchy, unrelated.

**Main failure modes (directly applicable to Scribe):**
1. **Overemphasized similarity**: "Relational definitions can sometimes emphasize similarities or overlaps that are not relevant to the actual relationship." → A system may merge "conversion rate" (sales) and "conversion rate" (currency exchange) because both involve ratios.
2. **Generic definitions**: GPT-generated definitions trend "vanilla" and miss the nuance that distinguishes near-synonyms. → "LTV" and "CAC payback period" both relate to customer value but are not coreferent.
3. **Context fixation**: Models over-rely on the narrow context window, "making definitions focused on too narrow aspects that prevent recognizing broader abstractions." → A claim from a single 3-minute transcript segment may not provide enough context to determine if its concept is an alias or a distinct node.

**Sources:**
- [SciCo: Hierarchical Cross-Document Coreference for Scientific Concepts (arXiv 2104.08809)](https://arxiv.org/abs/2104.08809)
- [Scientific Cross-Document Coreference and Hierarchy with Definition-Augmented Relational Reasoning (arXiv 2409.15113)](https://arxiv.org/html/2409.15113v1)

---

## 11. Entity Resolution in Knowledge Graphs: The Production Gap

[Entity-resolved knowledge graphs (Towards Data Science)](https://towardsdatascience.com/entity-resolved-knowledge-graphs-6b22c09a1442/) document the production failure mode: the same entity appears under different identifiers across datasets — "Tom Riddle" in one, "T.M. Riddle" in another, "Lord Voldemort" in a third. The resolution technique spectrum runs from:
- **Fuzzy matching** (lightweight, handles typos and abbreviations)
- **Semantic embedding + clustering** (handles synonym-level variation — "LGBT" / "LGBTQ+")
- **Graph topology** (uses relationship structure as a disambiguation feature when names alone are ambiguous)

The unsolved challenge: "multiple languages and international characters across terabytes of data" — but for Scribe the relevant version is: a speaker's idiosyncratic vocabulary vs. the canonical term, where no training data exists for that speaker's specific usages.

**ODKE+** ([arXiv 2509.04696](https://arxiv.org/html/2509.04696v1)) is a recent LLM-based open-domain knowledge extraction system with modules for corroboration and normalization. It achieves ~48% overlap with third-party KGs and reduces update lag by 50 days. Its normalization pipeline is the closest production analogue to what Pass D needs.

**Sources:**
- [Entity-Resolved Knowledge Graphs — Towards Data Science](https://towardsdatascience.com/entity-resolved-knowledge-graphs-6b22c09a1442/)
- [ODKE+: Ontology-Guided Open-Domain Knowledge Extraction with LLMs (arXiv 2509.04696)](https://arxiv.org/html/2509.04696v1)

---

## 12. Direct Answers to the Three Sub-Questions

### (a) Speaker-Specific Terminology

Every domain studied has this problem in some form, and none has solved it automatically:

- **PKM tools** (Obsidian/Logseq/Roam): aliases must be manually added. No tool auto-detects that two speakers coined different labels for the same concept.
- **Zettelkasten**: human judgment at filing time is the only mechanism.
- **Wikipedia**: "words to watch" lists encode community-negotiated resolutions, but require editorial maintenance.
- **BookNLP**: handles name variants of the *same speaker* (Tom vs. Mr. Sawyer), but not concept-level synonym resolution across speakers.
- **SciCo**: the best automated approach, using definition-augmented relational reasoning, but still fails at "context fixation" — a short transcript segment may not provide enough signal.

**Recommendation for Scribe:** At extraction time (Pass A), each claim should capture the speaker's exact surface form of the concept alongside the canonical `topic` field. The surface form becomes an alias candidate. Pass D then decides, per alias pair, whether the relationship is coreference (same node) or near-miss (separate nodes with a cross-source agreement edge).

### (b) Same Term, Different Meanings Across Sources

This is Wikipedia's core disambiguation problem and the hardest case for automated resolution:

- Wikipedia's solution: hatnotes + qualifier pages + disambiguation pages. Requires editorial judgment.
- SciCo's solution: pairwise relational definition generation. Expensive; fails on overemphasized similarity.
- Corporate wikis: no solution — they simply accumulate the collision and degrade over time.
- Scribe's existing solution: `contradictions.json` with `kind: contradiction` edges. The CLAIM-DEFINITION.md §6 table is the "words to watch" list for extraction prompts.

**Gap:** The current Scribe pipeline does not yet detect *within-term* sense collisions (two sources using "funnel" to mean different structural concepts). This needs a sense-disambiguation pass that checks whether two claims sharing a topic node are actually about the same concept or whether the topic node needs splitting.

### (c) Drift Over Time as New Sources Arrive

None of the systems studied handle this well automatically:

- **Ontology drift** (SemaDrift, OntoDrift frameworks): detect drift by comparing version N to N+1, but require a complete re-run over all prior data.
- **Knowledge graph embeddings** (Chen et al., 2020): use embedding-space movement as a drift signal. Promising but requires a stable embedding model across versions.
- **Logseq/Obsidian**: no drift detection — new notes simply add to the accumulation.
- **Corporate wikis**: governance (ownership + approval) is the only prevention.

**Recommendation for Scribe:** After each batch of new sources is ingested, run a **concept-stability check** on the topic graph: for each canonical topic node, compute embedding similarity between the centroid of claims in batch N and the centroid of new claims in batch N+1. A cosine distance above a threshold flags the node for human review ("this concept may have split or shifted meaning"). This is the SemaDrift morphing-chain approach adapted to Scribe's incremental ingestion model.

---

## 13. Summary Table: What Each System Can and Cannot Do

| System | Handles speaker variants | Detects same-term/diff-meaning | Handles drift over time | Automation level |
|---|---|---|---|---|
| Matuschak evergreen notes | Human (choose canonical handle) | Human (concept-oriented filing) | Human (retroactive refiling) | Manual |
| Wikipedia MOS | Editorial (hatnotes + disambiguation pages) | Editorial (words to watch) | Editorial (ongoing) | Manual + policy |
| Zettelkasten | Tags + structure notes (manual) | Not addressed | Not addressed | Manual |
| Obsidian aliases | Manual `aliases:` frontmatter | Not automated | Not automated | Manual |
| Logseq `alias::` | Partially (fragmentation bug) | Not automated | Not automated | Partial |
| Tana supertags | Schema enforcement only | Not automated | Not automated | Schema only |
| BookNLP | Name clustering (within-doc) | Out of scope | Out of scope | Automated (names only) |
| SciCo H-CDCR | Automated (cross-doc) | Partially (overemphasized similarity failure) | Not addressed | Partially automated |
| ODKE+ | Automated (normalization module) | Partially | Not addressed | LLM-automated |
| **Scribe (current)** | Surface form in `attribution` | `contradictions.json` edges | **Gap — not yet addressed** | LLM-assisted |

---

## 14. Key Implications for Scribe's Knowledge Graph Design

1. **Surface form must be a first-class field.** Every claim must carry the speaker's original term alongside the canonical topic label. This is already in CLAIM-DEFINITION.md §3 (`attribution.speaker`, `topic`), but the speaker's surface-form vocabulary needs an explicit `speaker_term` field to enable alias detection downstream.

2. **Alias resolution is a separate pass, not part of extraction.** SciCo and BookNLP both demonstrate that name clustering must precede claim inference. Scribe needs a Pass A½ or Pass D sub-step that clusters topic labels by embedding similarity before cross-source agreement scoring.

3. **Sense disambiguation is not alias resolution.** The same word meaning different things (Wikipedia's problem) is harder than the same concept having different names (BookNLP's problem). Scribe's `contradictions.json` handles the former; the latter needs an alias/merge mechanism.

4. **Incremental ingestion without drift tracking degrades the graph.** A concept-stability check keyed on embedding centroid movement across batches is the minimum viable drift detector. Without it, the graph silently accumulates concept collisions as new batches arrive.

5. **The "words to watch" pattern (CLAIM-DEFINITION.md §6) should be expanded.** The current ambiguity table covers meta-vocabulary (fact, insight, principle). It should also grow a per-domain lexicon of known speaker-specific synonyms: Hormozi's "front-end offer" = general "lead magnet" = others' "entry-point product". This is the application-layer equivalent of Wikipedia's disambiguation pages.

---

## Sources Cited

1. [Evergreen notes should be concept-oriented — Andy Matuschak](https://notes.andymatuschak.org/Evergreen_notes_should_be_concept-oriented)
2. [Concept handles, after Alexander — Andy Matuschak](https://notes.andymatuschak.org/z3b7sidNrEkNaY9qfGwZjwz)
3. [Tools for Thought as Cultural Practices, not Computational Objects — Maggie Appleton](https://maggieappleton.com/tools-for-thought)
4. [A Brief History & Ethos of the Digital Garden — Maggie Appleton](https://maggieappleton.com/garden-history)
5. [Wikipedia:Disambiguation](https://en.wikipedia.org/wiki/Wikipedia:Disambiguation)
6. [Wikipedia:Consistency](https://en.wikipedia.org/wiki/Wikipedia:Consistency)
7. [Wikipedia:Manual of Style/Words to watch](https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Words_to_watch)
8. [Wikipedia:Manual of Style/Disambiguation pages](https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Disambiguation_pages)
9. [Zettelkasten.de — Kinds of Ties Between Notes](https://zettelkasten.de/posts/kinds-of-ties/)
10. [Zettelkasten Forum — Identifying patterns in types of notes](https://forum.zettelkasten.de/discussion/1729/identifying-patterns-in-different-types-of-notes-and-how-to-optimally-design-outline-notes)
11. [The Importance of Aliases — Gödel's (Alexander Rink)](https://www.goedel.io/p/the-importance-of-aliases)
12. [Logseq alias fragmentation issue #4342](https://github.com/logseq/logseq/issues/4342)
13. [Roam alias feature request issue #67](https://github.com/Roam-Research/issues/issues/67)
14. [The Power of Supertags in Tana — Medium / IntelliBoosters](https://medium.com/intelliboosters/the-power-of-supertags-in-tana-revolutionizing-personal-knowledge-management-69245022e7f0)
15. [BookNLP GitHub — booknlp/booknlp](https://github.com/booknlp/booknlp)
16. [SciCo: Hierarchical Cross-Document Coreference for Scientific Concepts — arXiv 2104.08809](https://arxiv.org/abs/2104.08809)
17. [Scientific Cross-Document Coreference and Hierarchy with Definition-Augmented Relational Reasoning — arXiv 2409.15113](https://arxiv.org/html/2409.15113v1)
18. [Entity-Resolved Knowledge Graphs — Towards Data Science](https://towardsdatascience.com/entity-resolved-knowledge-graphs-6b22c09a1442/)
19. [ODKE+: Ontology-Guided Open-Domain Knowledge Extraction with LLMs — arXiv 2509.04696](https://arxiv.org/html/2509.04696v1)
20. [Do you catch my drift? Concept shift in knowledge graphs — ACM DL](https://dl.acm.org/doi/fullHtml/10.1145/3587259.3627555)
21. [Knowledge graph embeddings for dealing with concept drift — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1570826820300585)
22. [Ontology drift — Wikipedia](https://en.wikipedia.org/wiki/Concept_drift)
