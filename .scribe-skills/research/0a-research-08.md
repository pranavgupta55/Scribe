# Research Note 08: Claim Extraction in NLP — Grounding CLAIM-DEFINITION.md in Empirical Literature

**Question strand:** What does the NLP/ML discipline actually mean by "claim", how do pipelines distinguish claims from non-claims, what schemas do they use, and what inter-annotator agreement is achievable?

---

## 1. How the Literature Defines "Claim" vs Non-Claim

### 1.1 ClaimBuster's Three-Way Taxonomy (Hassan et al., KDD 2017)

The most-cited operational taxonomy for claim detection uses three mutually exclusive categories applied to political debate sentences [S1]:

| Class | Definition |
|---|---|
| **NFS — Non-Factual Sentence** | Subjective sentences: opinions, beliefs, declarations, questions. *Cannot be verified against the world.* |
| **UFS — Unimportant Factual Sentence** | Factual but not check-worthy — the general public has no strong interest in its truth value. |
| **CFS — Check-worthy Factual Sentence** | Factual and of public interest — worth dispatching to a fact-checker. |

The key discriminator between NFS and CFS is **verifiability** (is there a fact-of-the-matter?) combined with **public interest** (would knowing whether it is true/false change anything?). Questions are NFS because they do not assert a truth-value. Definitions are NFS if they merely gloss common usage; they become UFS or CFS only when the speaker is asserting a non-standard usage as a fact about the world.

The ClaimBuster dataset: 20,617 sentences from U.S. presidential debate transcripts — 13,671 NFS, 2,097 UFS, 4,849 CFS.

### 1.2 FEVER — Claims as Mutated Wikipedia Sentences (Thorne et al., NAACL 2018)

FEVER constructs 185,445 claims by having annotators *mutate* Wikipedia sentences using six operations: negation, paraphrase, entity substitution (similar/dissimilar), relation substitution, generalization, and specificity increase [S2, S3]. Each claim is then independently labeled SUPPORTED / REFUTED / NOTENOUGHINFO against Wikipedia.

FEVER's implicit claim definition: **a standalone declarative sentence describing a single piece of information, not trivially verifiable from its surface form**. The mutation step operationalizes "not trivially verifiable" — the claim must require genuine evidence retrieval, not just string matching.

What FEVER excludes as claims: FEVER does not categorize non-claims explicitly (it starts from Wikipedia sentences that are already factual). But its mutation guidelines implicitly exclude questions, imperatives, and pure definitions by requiring a truth-apt declarative form.

Inter-annotator agreement on LABEL (not on claim-vs-non-claim): **Fleiss κ = 0.6841** on the three-way classification task [S2].

### 1.3 SciFact — Expert-Written Claims in Biomedicine (Wadden et al., EMNLP 2020)

SciFact defines a claim as **"an assertion about a single biomedical entity or process"** written by a domain expert [S4, S5]. Claims are curated, not extracted from text: expert annotators write claims about biomedical findings and then verify them against a 5,183-abstract corpus.

SciFact schema (claim node):
- **claim text** — the assertion in natural language
- **evidence label** — SUPPORTS / REFUTES / NOINFO per abstract
- **rationale spans** — sentence-level evidence from the abstract that justifies the label

Inter-annotator agreement on LABEL: **Cohen's κ = 0.75** on 232 re-annotated claim-abstract pairs [S6]. For comparison, biomedical claim annotation in Twitter posts reported pairwise Cohen's κ of 0.630–0.678, Fleiss κ = 0.629 [S6].

The SCIVER shared task (NAACL 2021) used SciFact directly and produced +23 F1 improvement from 11 competing systems but did not revise the underlying claim schema [S7].

### 1.4 AIDA — Formal Schema for Scientific Claims (Kuhn, 2013/2018)

The AIDA schema (Atomic, Independent, Declarative, Absolute) was developed for organizing scientific claims in nanopublications [S8, S9]. It defines a valid claim as satisfying four criteria simultaneously:

| Criterion | Operational meaning |
|---|---|
| **Atomic** | Describes one thought; cannot practically be decomposed further |
| **Independent** | Stands on its own without external references (no dangling pronouns, no presupposed context) |
| **Declarative** | Complete sentence that ends with a period; in principle truth-apt (could be true or false) |
| **Absolute** | Describes the core fact ignoring epistemic hedges about certainty |

Questions, imperatives, and definitions fail the Declarative test (questions don't assert a truth value; imperatives are not truth-apt). Examples and anecdotes fail Atomic (they are narratives, not single-fact assertions). Opinions with no empirical content fail Declarative / Absolute (they can't, even in principle, be made true or false by evidence).

AIDA is the theoretical ancestor of the AIDA framework cited in 2024 claim-quality papers as the evaluation standard [S10].

### 1.5 Multi-Dataset Comparative Analysis (Hasanain et al., 2024; Pikuliak et al., 2024)

A 2024 survey paper examining ClaimBuster, CLEF CheckThat!, EnvironmentalClaims, NewsClaims, and PoliClaim found that datasets converge on two perpendicular dimensions [S11, S12]:

- **Verifiability dimension**: Is there a fact-of-the-matter (NFS vs factual)?
- **Check-worthiness dimension**: Is the fact of public interest (UFS vs CFS)?

These are **not the same axis**. A claim can be verifiable but unimportant (UFS). Check-worthiness is context-dependent (what is check-worthy in a political debate may be unimportant in a cooking video). Verifiability is more stable.

Opinion/subjective sentences are consistently the hardest boundary case because "opinions with subjective components can also be factual claims if they explicitly present objectively verifiable facts" [S12]. The operative test is: does the sentence **explicitly present** a verifiable fact, or does it merely express a judgment about one?

---

## 2. Schemas Used

### Schema A — Atomic Claim (dominant in LLM-era pipelines)

The minimal schema used in decomposition pipelines (MiniCheck, FActScore, LLM fact-checking):

```
claim_text: str          # single self-contained sentence
source_span: str         # the passage segment it was extracted from
```

Properties required: **Atomicity** (one relation or property), **Fluency**, **Decontextualization** (interpretable without context), **Faithfulness** (nothing added beyond source) [S10].

IAA for these four properties using Gwet's AC1 (preferred over Krippendorff for imbalanced data [S10, S13]):
- Atomicity: **0.95**
- Fluency: **0.86**
- Decontextualization: **0.85**
- Faithfulness: **0.83**

Note: Krippendorff's α for the same task produced "abysmally low" values (−0.01 to 0.75) due to class imbalance. Gwet's AC1 is the recommended metric for tasks where most candidates are positive.

### Schema B — Claim + Evidence Span (FEVER/SciFact standard)

```
claim_text: str
evidence_label: SUPPORTED | REFUTED | NOTENOUGHINFO
evidence_spans: List[str]    # rationale sentences from source document
```

FEVER additionally records the Wikipedia page + sentence index for each evidence span, forming a claim-retrieval-verification triple.

### Schema C — Claim + Frame Elements (not yet mainstream)

Some structured-prediction work decomposes claims into frame elements (subject, predicate, object, conditions, quantifier), but this is not a dominant pattern in deployed pipelines as of 2024–2025. The closest mainstream representation is the **NLI triple** (claim, evidence, label), not a frame-semantic decomposition.

---

## 3. Inter-Annotator Agreement — Summary Table

| Task | Dataset | Metric | Value | Notes |
|---|---|---|---|---|
| Claim label (3-way: S/R/NEI) | FEVER | Fleiss κ | **0.684** | On 185K claims [S2] |
| Claim label (2-way: S/R) | SciFact | Cohen's κ | **0.75** | 232 re-annotated pairs [S6] |
| Biomedical Twitter claim label | Various | Fleiss κ | **0.629** | [S6] |
| Check-worthiness (binary CFS vs rest) | CLEF CheckThat! | Fleiss κ | **0.70–0.75** | Political text [S11] |
| Check-worthiness | PoliClaim | Cohen's κ | **0.69** | Political speeches [S12] |
| Check-worthiness | EnvironmentalClaims | Krippendorff α | **0.47** | Moderate; domain harder |
| Check-worthiness | NewsClaims | Krippendorff κ | **0.405** | Moderate; topic-specific |
| Claim quality dimensions | Decomposition study | Gwet's AC1 | **0.83–0.95** | Atomicity/fluency/decontex/faithfulness [S10] |
| Claim vs non-claim (binary) | Various | Krippendorff α | **0.46–0.70** | Moderate; improves with training [S10, S11] |

**Key empirical pattern:** Binary claim-vs-non-claim annotation converges in the **0.60–0.75 range** (moderate to substantial). Fine-grained check-worthiness judgment is harder, falling to 0.40–0.50. Agreement on *quality dimensions* of already-extracted claims (atomicity, decontextualization) achieves 0.83–0.95 when criteria are explicit. The takeaway: **the hardest judgment is "is this check-worthy?"; the easier judgment is "does this already-accepted claim satisfy the atomicity/decontextualization criteria?"**

---

## 4. Key Distinctions the Literature Has Settled

### Claims vs Opinions
The literature consistently treats opinions as NFS not because they cannot be held more or less firmly, but because they **do not have an empirical truth condition** that an external source can SUPPORT or REFUTE. "X is the best strategy" is NFS. "X strategy produced 30% higher conversion in Hormozi's 2019 cohort" is CFS. The line is empirical truth-aptness, not certainty.

### Claims vs Definitions
Definitions are NFS under ClaimBuster's taxonomy. But the NLP literature has a nuance: when a speaker is asserting *a non-standard definition as a fact about how a term should be used or is used in a specialized community*, that assertion becomes a CFS. The definition of "funnel" as used by Hormozi — which differs from the standard marketing definition — is a CFS because a fact-checker could verify: does Hormozi's usage match his described meaning?

### Claims vs Examples/Anecdotes
Anecdotes fail the Atomic criterion: they are multi-event narratives, not single-fact assertions. They also often fail Decontextualization (the story requires the surrounding context to be meaningful). In SciFact, examples embedded in abstracts are treated as evidence *for* claims, not as claims themselves — exactly the relationship our CLAIM-DEFINITION.md already encodes.

### Claims vs Questions
Questions are explicitly NFS in all reviewed taxonomies. A rhetorical question can *imply* a claim but is not itself a claim. In practice, claim extractors either ignore questions or convert implied claims from rhetorical questions into declarative form.

### Claims vs Practices/Imperatives
Imperatives ("always close on the call") are NFS in the ClaimBuster taxonomy — they are not truth-apt (you cannot SUPPORT or REFUTE an imperative). This is precisely why CLAIM-DEFINITION.md §2 puts imperatives in `practices[]` rather than `claims[]`.

---

## 5. What the Literature Reveals is Missing from Our Current Definition

Our CLAIM-DEFINITION.md already handles most of the above correctly:
- Falsifiability (§1) maps to verifiability in the literature
- Attribution (§1, §3) is present but not modeled in most NLP pipelines (they focus on text truth, not speaker credibility)
- Conditions (§3) are unique to our domain (practitioner knowledge graphs vs. factual news claims)
- Practices vs Claims (§2, A8) maps directly to NFS in ClaimBuster

**The one thing the NLP literature surfaces that our definition does not yet address explicitly:**

**Decontextualization as a first-class property.** In every evaluated pipeline — AIDA's "Independent" criterion, SciFact's evidence spans, the FEVER mutation step, and the 2024 decomposition literature — a claim must be **interpretable and truth-evaluable without access to its surrounding context**. This is distinct from "standalone-meaningful" (which we use) because it is a stronger, more testable criterion: not just "does it read correctly out of context" but "can a reader with no access to the source document determine what world-state would make this claim true or false?"

Concretely: "this works 30% better" fails decontextualization even though it reads as a sentence. "Alex Hormozi's same-call close technique for B2B service sales ≥$5k produced 30% higher conversion than scheduling a follow-up, in Acquisition.com's 2019 cohort" passes. The failing version is in fact our A3 failure mode (decontextualized number) and our A1 failure mode (unbound actor), but our definition does not state the decontextualization requirement as a *positive* first-class property of the claim text field.

---

## Bottom Line

**Single concrete refinement to CLAIM-DEFINITION.md:**

In **§3 (Claim shape)**, the `text` field description should add an explicit **Decontextualization criterion**: a claim text must be evaluable for truth or falsity by a reader who has no access to the source document. This means all actors, conditions, time periods, and comparison baselines must be named *inside the claim text itself* — not merely implied by the surrounding section or available from `attribution`. This is the AIDA "Independent" criterion and the most consistently validated property across all reviewed pipelines (Gwet's AC1 = 0.85 in human annotation [S10]).

The current wording — *"Reads correctly out of context"* — is necessary but insufficient. The AIDA-aligned criterion adds: *"A reader with no access to the source file must be able to identify what evidence would settle whether the claim is true or false."*

This refinement would make A1, A3, A10, and A11 failures *derivable* from a single positive property of the text field, rather than being listed as separate failure modes. It also gives the extraction agent a single clean test to apply before checking any of the 20 failure modes.

---

## Sources

- [S1] Hassan, N. et al. "Toward Automated Fact-Checking: Detecting Check-worthy Factual Claims by ClaimBuster." KDD 2017. https://www.kdd.org/kdd2017/papers/view/toward-automated-fact-checking-detecting-check-worthy-factual-claims-by-cla
- [S2] Thorne, J. et al. "FEVER: A Large-scale Dataset for Fact Extraction and VERification." NAACL 2018. https://aclanthology.org/N18-1074/
- [S3] arXiv preprint: https://arxiv.org/abs/1803.05355
- [S4] Wadden, D. et al. "Fact or Fiction: Verifying Scientific Claims." EMNLP 2020. https://aclanthology.org/2020.emnlp-main.609/
- [S5] SciFact dataset card: https://huggingface.co/datasets/allenai/scifact
- [S6] SciFact IAA and biomedical claim annotation: https://pmc.ncbi.nlm.nih.gov/articles/PMC10919922/
- [S7] Wadden, D. et al. "Overview and Insights from the SciVer Shared Task on Scientific Claim Verification." ACL 2021. https://aclanthology.org/2021.sdp-1.16/
- [S8] Kuhn, T. "Using the AIDA Language to Formally Organize Scientific Claims." 2018. https://arxiv.org/abs/1806.01507
- [S9] AIDA GitHub: https://github.com/tkuhn/aida
- [S10] "Claim Extraction for Fact-Checking: Data, Models, and Automated Metrics." arXiv 2025. https://arxiv.org/html/2502.04955v1
- [S11] "Claim Check-Worthiness Detection: How Well do LLMs Grasp Annotation Guidelines?" arXiv 2024. https://arxiv.org/html/2404.12174v1
- [S12] "AFaCTA: Assisting the Annotation of Factual Claim Detection with Reliable LLM Annotators." arXiv 2024. https://arxiv.org/html/2402.11073
- [S13] "Counting on Consensus: Selecting the Right Inter-annotator Agreement Metric for NLP." arXiv 2026. https://arxiv.org/html/2603.06865
- [S14] "Document-level Claim Extraction and Decontextualisation for Fact-Checking." arXiv 2024. https://arxiv.org/html/2406.03239v2
- [S15] "Checkworthiness in Automatic Claim Detection Models: Definitions and Analysis of Datasets." arXiv 2020. https://arxiv.org/pdf/2008.08854
- [S16] "Towards Automated Factchecking: Developing an Annotation Schema and Benchmark for Consistent Automated Claim Detection." arXiv 2018. https://arxiv.org/pdf/1809.08193
- [S17] "A Closer Look at Claim Decomposition." arXiv 2024. https://arxiv.org/pdf/2403.11903
- [S18] "Check-worthy Claim Detection across Topics for Automated Fact-checking." arXiv 2022. https://arxiv.org/pdf/2212.08514
- [S19] "Claim Detection for Automated Fact-checking: A Survey on Monolingual, Multilingual and Cross-Lingual Research." arXiv 2024. https://arxiv.org/pdf/2401.11969
