# Claim Definition

This is the canonical definition of what a **claim** is in the Scribe knowledge graph. Adapted from the `ubiquitous-language` discipline (mattpocock/skills) — pick canonical names, list aliases to avoid, define what each term **is**.

The single most useful test for whether something is a claim: *"If this were false, what would change in how the reader acts or thinks?"* If the answer is "nothing", it is not a claim.

## §1 — What a claim is

**Claim**:
A load-bearing assertion about how the world works, anchored to a named speaker, in a named source, that — if it were false — would change what the reader does or believes. Must be (a) specific, (b) standalone-meaningful, (c) falsifiable in principle, (d) source-attributed.
_Avoid_: "fact" (suggests verified truth; we don't verify, we attribute), "insight" (suggests trade-paperback marketing voice), "key takeaway" (we use this only as a section heading, not as a node type), "lesson", "principle" (overloaded — see Framework below).

## §2 — What a claim is **not**

Each of the following is a useful node type, but **not a claim**. They live under claims, never alongside them.

**Definition**:
A statement of what a term means, when the speaker is using it in a non-standard way. Standard dictionary definitions don't survive — they belong in the topic's `definition` field, not as a claim.
_Avoid_: using "definition" for any restatement of a term in its common sense.

**Framework / Mental Model**:
A named, structured way of organizing a set of claims (e.g. "Core Four Methods", "Bullseye Framework"). The framework name itself is a node; the load-bearing assertions inside the framework are separate claim nodes that link up to it.
_Avoid_: "principle" (use Framework when there is named structure, Mechanism when there is a stated causal chain, Claim when there is just one assertion).

**Mechanism**:
A causal-chain statement of the form "X causes Y because Z". The because-clause must be present and non-circular. Pure correlation does not qualify.
_Avoid_: "explanation", "why".

**Anecdote / Example**:
A specific story, told by the speaker, that illustrates a claim. Lives as a child of the claim, not a sibling. May contain numbers but they are illustrative, not load-bearing.
_Avoid_: presenting an anecdote as a claim. The claim is what the anecdote illustrates, not the anecdote itself.

**Quantified Axis**:
A gradient with at least two anchor points along a named dimension. The shape is "Along axis A, region 1 has value X, region 2 has value Y, region 3 has value Z." A single (axis, value) pair is **not** an Axis — it is a fragment and should either be embedded inside a real Axis or dropped.
_Avoid_: floating numbers, isolated benchmarks, "rules of thumb" without their domain of applicability.

**Metaphor / Analogy**:
A named comparison between two domains. The node must declare itself as a metaphor in its title (e.g. "Mountain Climbing Metaphor"). Aliasing the metaphor name → the abstract concept it represents lets later agents find it.
_Avoid_: implicit metaphors smuggled into claim text without flagging.

**Counterexample**:
A case the speaker explicitly raises as falsifying or limiting a claim. Lives under the claim it bounds.
_Avoid_: using counterexample to mean a contrasting case from another speaker — that is a **Contradiction**.

**Contradiction**:
A disagreement between two named speakers / sources on a load-bearing claim. Stored as an edge in `connections.json` with `kind: contradiction`, not as a standalone node.
_Avoid_: labeling mere differences in emphasis as contradictions.

**Practice / Advice**:
A `should-do` imperative without a backing claim. Lives in a separate `practices[]` array per topic, **not** in `claims[]`. Surfaces in the chat layer as suggestion-grade, not load-bearing.

**Implementation Detail**:
A concrete step in executing a practice or framework. Lives as a leaf, never floats free.

## §3 — Claim shape (what every claim node carries)

```yaml
claim:
  text: |
    One-to-three sentence prose. Reads correctly out of context. Names every
    actor/company/product/time-period. Surfaces conditionals. Includes mechanism
    if causal. Quotes from the source verbatim or not at all (no paraphrase in
    quotes).
  type: assertion | mechanism | comparison | conditional | quantified
  attribution:
    speaker: "Alex Hormozi"           # named individual
    source_file: "helping_..._scale_8C_6qojTA78.txt"
    section: "Pricing for Mid-Market" # the LLM-extracted section title
    transcript_offset: "23:40"        # approximate, from .meta.json
  conditions:                          # required when the claim is rule-shaped
    - "B2B service sales"
    - "price ≥ $5,000"
    - "single-call close possible"
  mechanism: |                         # required when type=mechanism or causal
    Stating a price commits the buyer to defend their decision, reducing
    post-call drift.
  numbers:                             # claims with numbers fold their axis in
    axis: "monthly ad spend"
    anchors:
      - { region: "SMB <$10k/mo",  value: "5-15 creatives" }
      - { region: "mid $10k-100k", value: "50-100 creatives" }
      - { region: "ent $100k+",    value: "200-1000 creatives" }
  bounded_by:                          # named counterexamples / non-applicability
    - "Does not apply to self-serve SaaS"
    - "Does not apply to in-store retail"
  topic: "Lead Generation"             # canonical parent topic (one and only one)
  hierarchy_role: "Claim"              # see HIERARCHY.md
  examples: ["..."]                    # child anecdote node ids
  cross_source_agreement: []           # filled by Pass D
  cross_source_contradiction: []       # filled by Pass D
```

Fields are optional except where noted. Missing `attribution` → reject. Missing `conditions` on a rule-shaped claim → reject (or downgrade to `practice`). Missing `mechanism` on a causal claim → reject.

## §4 — Why we include a node

Three legitimate reasons to add any node:

1. **It changes how a reader acts** — they would now do something differently in their business / craft / decision.
2. **It triangulates with other sources** — the claim agrees with, builds on, or contradicts a load-bearing claim from another source, and the cross-source link itself carries information.
3. **It explains a mechanism** — it gives a *why* that a reader can apply to novel situations the source did not cover.

If a candidate node does none of these, drop it.

> The user's question — *"will there be some kind of 'vote' where if multiple sources have the same or a similar claim then we validate it as true?"* — is reason #2 above. We do not assert truth, but we surface **agreement weight** and **contradiction weight** as edge attributes. A claim with 4 cross-source agreements and 0 contradictions is shown with a higher confidence than a claim asserted by one source. Confidence is rendered, not promoted to truth.

## §5 — Example dialogue (the test of a clean glossary)

> **Dev:** "Is 'always close on the call' a **Claim**?"
> **Domain expert:** "Not as written. Without conditions it's a **Practice** at best — there's no audience, no price band, no mechanism. Add those and it becomes a Claim."
> **Dev:** "What if Hormozi says 'in our B2B service-business cohort at ≥$5k price points, closing on the same call doubled conversion vs. scheduling a follow-up, because the buyer commits during the call'?"
> **Domain expert:** "Now it's a Claim of type `mechanism`. Audience and price band are **conditions**. 'Doubled vs. scheduling' is the **comparison**. 'Buyer commits during the call' is the **mechanism**. The original 'always close' bumper sticker can hang off it as a **Practice** alias."
> **Dev:** "And the bumper sticker on its own?"
> **Domain expert:** "Goes in the `practices[]` list under the same topic. It is searchable, it is renderable, but it is not a Claim and it does not vote on truth."

## §6 — Flagged ambiguities in current data

These are the terms the current extraction (`PROMPT.md`) and connection (`CONN_PROMPT.md`) prompts use ambiguously. Future agents must use the canonical term and treat the aliases as smells.

| Used as            | Canonical term                | Why                                                                                                |
| ------------------ | ----------------------------- | -------------------------------------------------------------------------------------------------- |
| "fact"             | **Claim**                     | We don't verify, we attribute. "Fact" implies truth.                                               |
| "insight"          | **Claim** or **Mechanism**    | Vague marketing voice. Pick a specific node type.                                                  |
| "key takeaway"     | (section heading only)        | Useful as a section title in rendered output. Not a node type.                                     |
| "principle"        | **Framework** or **Mechanism**| Overloaded. Use Framework when there is named structure, Mechanism when causal.                    |
| "rule"             | **Claim** with `conditions`   | A "rule" without conditions is a Practice.                                                         |
| "best practice"    | **Practice** (not a Claim)    | Imperative without conditions. Promote to Claim only when conditions + mechanism appear.           |
| "method"           | **Framework** or **Practice** | Pick by structure.                                                                                 |
| "founder", "CEO", "owner" (generic) | **named entity** | Always resolve to the person + company. If unresolvable, the claim fails A1 and is rejected.   |
