# Node-Quality Rubric

The single failure pattern across the current graph is that **specific** has been mistaken for **meaningful**. A node-extraction agent that hits all the surface-level rules (no quotes around quotes, no nested arrays) can still produce a wall of useless content. This document enumerates every failure mode found in the current graph and the positive shape we want instead. Use it as the few-shot reference for every extraction, normalization, and verification agent in the pipeline.

The rubric has three sections:

- **Section A — Failure modes.** What "bad" looks like, with verbatim examples from the current graph, the reason it fails, and the rewrite that would have passed.
- **Section B — Positive signals.** What "good" looks like — properties that distinguish a load-bearing node from filler.
- **Section C — Scoring matrix.** A 0/1 checklist a Haiku agent can apply to any candidate node.

---

## Section A — Failure modes (the must-reject set)

Each failure mode has: the **name**, a **definition**, a verbatim **bad example** from our current data, **why it fails**, and what the **rewrite** would look like (or the right action: drop, merge, demote).

### A1 — Ambiguous actor reference ("the founder did X")

**Definition.** A claim that uses a generic role-word ("the founder", "the CEO", "the team") instead of the named entity, with no surrounding context that identifies whom it refers to.

**Bad:** "Founder shifted from doing all sales calls to hiring a sales team after revenue crossed $2M."

**Why it fails.** When this node is surfaced via search or pasted into another conversation, the reader has no idea which founder. The information is unfalsifiable and uncomparable to other sources.

**Rewrite.** "**Alex Hormozi** (Gym Launch, 2018) shifted from doing all sales calls personally to hiring a 4-person sales team after monthly revenue crossed roughly $2M." — names the entity, names the company, anchors a time, and qualifies the number ("roughly") instead of presenting it as exact.

**Repair rule for normalization.** If a claim contains an unbound role-word (founder, CEO, owner, the team, the buyer, the customer), reject the claim or resolve the referent from the surrounding section. Never propagate an unresolved generic actor.

### A2 — Specific-but-not-load-bearing detail (the feature-list claim)

**Definition.** A "claim" that consists of a list of concrete tactics or features, presented as though it were an assertion, but with no load-bearing point — nothing would change in the reader's behavior if the list were different.

**Bad:** "Offer white-labeled white-wash, pre-written emails, custom landing page, team setup support to reduce friction."

**Why it fails.** Specific, yes. Claim-shaped, no. It is a checklist of features dressed in a sentence. There is no comparison, no axis, no implication. If the list contained different items, the "claim" would be just as true and just as useless.

**Rewrite.** Either reshape it as a load-bearing claim — "**Brand-licensing offers** that include a turnkey marketing kit (landing page + email sequences + done-for-you onboarding) convert better than offers that require the licensee to build their own assets, because they remove the licensee's #1 perceived risk: 'I won't know what to do once I sign.'" — or **demote it to an example** under a real claim.

**Repair rule.** A node is a **claim** only if you can answer "if this turned out to be false, what would change?" If the answer is "nothing — it's just a feature list", demote it to `examples[]` or `implementation_details[]` under its parent claim.

### A3 — Decontextualized number (the axis-less data point)

**Definition.** A numerical fact stated in isolation, with no axis (the dimension along which the number varies) and no comparison points.

**Bad:** "Mid-market has immediate pain (need 50-100 creative pieces)."

**Why it fails.** What is the axis? Company size? Ad spend? Industry? What does small-market need — 10? What does enterprise need — 1000? Without the axis, 50-100 is unmemorable and unverifiable. A reader can't decide whether they are mid-market or whether they need creative pieces at all.

**Rewrite.** Either embed the full axis — "**Creative-asset need scales roughly with monthly ad spend.** SMB (<$10k/mo spend): 5-15 creatives. Mid-market ($10k-$100k/mo): 50-100. Enterprise ($100k+/mo): 200-1000. The number doubles roughly every order of magnitude in spend." — or drop the bare number entirely.

**Repair rule.** Numbers must appear inside a **quantified-axis node** (see CLAIM-DEFINITION.md §3). A standalone "X is Y" with a quantity is a fragment, not a claim.

### A4 — Vague blanket statement (the "somewhere in the middle" claim)

**Definition.** A claim that asserts something is "moderate", "balanced", "the right amount", etc., without grounding the position with a quantity or comparison.

**Bad:** "Invest vs. consume must be in the middle somewhere, not extremes."

**Why it fails.** True of almost everything ("don't be extreme") and operationally useless. The reader cannot apply this. It does not connect to anything quantifiable.

**Rewrite.** Either ground it — "**Personal capital allocation, Naval Ravikant:** spend ~30-50% of free cash flow consuming (lifestyle, experiences), invest the remainder. Below 30% consume → burnout; above 60% → no compounding." — or drop the claim. The unrescuable version goes away.

**Repair rule.** If the central verb of a claim is *"should be moderate / balanced / in the middle / appropriate / reasonable"* and there is no number or comparator, reject.

### A5 — Atomized definition (one idea split across bullets)

**Definition.** A single coherent definition or argument that has been fragmented into multiple bullet points, where each bullet on its own is a meaningless stub.

**Bad:**
```
Key Takeaway
- Observable definition of learning
- Condition is constant, behavior changes
- If behavior doesn't change, learning didn't happen
```

**Why it fails.** No single bullet stands on its own. "Observable definition of learning" is a sentence fragment, not a claim. The reader's brain has to silently re-merge the three bullets back into one sentence to understand any of them.

**Rewrite.** One coherent sentence: "**Operational definition of learning (B.F. Skinner):** a change in behavior under a constant condition. If the condition is held constant and the behavior does not change, no learning has occurred — even if the subject reports understanding."

**Repair rule.** During section-rewrite, merge any cluster of bullets that share the same conceptual unit into a single prose sentence or two-sentence statement. Bullets are for **lists of distinct items**, not paragraphs cut into pieces.

### A6 — Atomized example (one anecdote split across bullets)

**Definition.** A single example or story split into per-event bullets, losing the narrative structure that gave it its meaning.

**Bad:**
```
Phone rings: taught to say Z instead of X
Phone rings again: if you say X, no learning
Phone rings again: if you say Z, learning occurred
```

**Why it fails.** Same as A5, but at the narrative level. The example was meant to be one short story that illustrates the operational definition; cutting it into three fragments destroys its illustrative power.

**Rewrite.** "**Example (Skinner).** A trainee is taught to answer the phone with greeting Z instead of their habitual X. On the next call, if they revert to X, no learning has occurred. If they say Z, learning has occurred — observable behavior, constant condition (phone ringing), changed response."

**Repair rule.** An "example" or "anecdote" is **one** node (prose paragraph), not a sequence of bullets. The node carries the full narrative arc.

### A7 — Self-referential definition (claim that restates the topic name)

**Definition.** A "claim" under a topic that simply restates the topic name as its content.

**Bad (topic: Lead Generation):** "Lead generation is the process by which businesses acquire prospective customers."

**Why it fails.** No new information. The topic name + a dictionary definition is not a claim; it is the topic label expanded into a sentence.

**Rewrite.** Drop it. The topic's `definition` field carries this once; claims must add information beyond the definition.

**Repair rule.** If a claim's noun-phrases are a strict superset of the topic name and it contains no verb beyond "is/refers to/means", reject.

### A8 — Hortative ("you should do X")

**Definition.** A claim phrased as imperative advice with no supporting evidence, mechanism, or conditional.

**Bad:** "Always be closing on every call."

**Why it fails.** Pure advice. Not falsifiable. Doesn't say *why*, *when it fails*, or *what alternative was tried and rejected*.

**Rewrite.** Either supply the supporting structure — "**Closing on every sales call** (Hormozi, in B2B service contexts $5k+): pitches without a close convert below 5%; pitches with an explicit close request convert 30-60% on the same offers. Mechanism: stating a price commits the buyer to defend their decision, reducing post-call drift." — or demote to `practices[]` (advice-only) rather than `claims[]`.

**Repair rule.** Imperatives without `because`-clauses are advice, not claims.

### A9 — Tautology / restated truism

**Definition.** A claim that is true by definition or trivially obvious to anyone in the domain.

**Bad:** "Customers who pay more are worth more to the business."

**Why it fails.** Adds no information; nobody in the audience disagrees.

**Repair rule.** Reject if the claim, restated with synonyms, would be accepted by every domain practitioner without evidence.

### A10 — Hidden conditional (load-bearing context dropped)

**Definition.** A claim that omits the conditions under which it holds, presenting a domain-specific rule as a universal.

**Bad:** "Always close on the call."

**Why it fails.** True for high-touch B2B service sales ≥$5k. False for self-serve SaaS, false for in-store retail, false for VC pitches. The hidden conditional ("for high-touch B2B service sales") is what makes the claim load-bearing; without it the claim is wrong half the time.

**Rewrite.** Surface the conditional: "**For high-touch B2B service sales (price ≥$5k, single-call close possible)**, always close on the same call rather than scheduling a follow-up; introducing a follow-up cuts conversion roughly in half (Hormozi's Acquisition.com cohort, 2019-2022)."

**Repair rule.** A claim about a behavior or rule must include its **conditions of applicability** (audience, price band, channel, stage of business, geography, time period — whichever is load-bearing).

### A11 — Speaker attribution missing

**Definition.** A claim that depends on the credibility/experience of its speaker but doesn't name them.

**Bad:** "It's better to hire from inside the industry than outside."

**Why it fails.** Who is asserting this, from what experience? Hormozi from one industry's hiring data is different from a McKinsey meta-study. The claim cannot be weighed without the source.

**Repair rule.** Every claim node carries `attribution: {speaker, source_file, transcript_offset}`. If the speaker matters (i.e. the claim is opinion/experience-based rather than mathematical), the rendered claim text must name them inline.

### A12 — Time anchor missing

**Definition.** A claim with implicit time-dependence ("we just raised prices", "the market is hot") that doesn't say when it was made.

**Repair rule.** Time-sensitive claims must carry the source's publication date in the rendered text, or be rejected.

### A13 — Universalized anecdote (single-case generalization)

**Definition.** A single personal experience presented as a universal principle.

**Bad:** "Founders who delegate sales early scale faster."

**Why it fails.** Either a single-sample anecdote dressed as a rule, or an under-cited claim. Without N, without the comparison group, this is anecdote-as-law.

**Repair rule.** Either include the cohort size and comparison ("In Acquisition.com's 60-business portfolio, founders who hired their first AE before $3M ARR reached $10M roughly 14 months faster on median than founders who held sales through $5M+") or mark as `experience-report` rather than `claim`.

### A14 — Survivorship-bias claim

**Definition.** A claim derived from sampling only successful instances, without the base rate.

**Bad:** "All top-1% earners journal every morning."

**Why it fails.** The non-top-1% earners who also journal aren't in the sample.

**Repair rule.** A claim asserting a property of a successful group must either name the non-success group it was compared against, or be downgraded to `correlation-noted` rather than `claim`.

### A15 — Correlation dressed as causation

**Definition.** A claim asserting X causes Y when only co-occurrence was observed.

**Repair rule.** If the source used hedges like "we noticed", "we found", "tends to", the rewrite must preserve those hedges. A claim with `kind: causal` requires explicit causal language from the speaker plus a mechanism statement.

### A16 — Mixed-grain claim (multiple ideas stuffed in one)

**Definition.** A claim that bundles two or three unrelated assertions in one sentence with conjunctions.

**Bad:** "Hire slowly, fire fast, raise prices every six months, and never give discounts unless they ask twice."

**Why it fails.** Four separate claims linked only by being from the same speaker. None can be evaluated individually because they sit inside one node.

**Repair rule.** Split into four claim nodes. Conjunctions are a smell.

### A17 — Definition-without-distinction

**Definition.** A claim that defines a term but the definition is not different from how the term is commonly used.

**Bad:** "A funnel is the customer journey from awareness to purchase."

**Why it fails.** Standard dictionary content. Not a claim.

**Repair rule.** Reject definitions that match the term's common usage. Definitions that *disambiguate* a specialized usage from the common one (e.g. "Hormozi uses 'funnel' to mean the **post-traffic-source** sequence only, excluding the awareness layer") survive as claims.

### A18 — Vague comparative ("better", "more effective", "higher quality")

**Definition.** A claim of the form "X is better than Y" without a stated metric.

**Bad:** "Cold email is better than cold calling."

**Why it fails.** Better on what axis? Conversion rate? Cost per reply? Speaker preference? Reader cannot use the comparison.

**Repair rule.** Comparisons require a metric, a number or range, and the conditions: "**Cold email outperforms cold calling on cost-per-meeting** in B2B SaaS targeting non-decision-makers (~$30-80/meeting vs. $200-400 for calling), but **underperforms** on close-rate-per-meeting (calling lands 20-30% close, email 8-12%) — Hormozi & Levels samples, 2022."

### A19 — Pseudo-quote (paraphrase presented as exact words)

**Definition.** Text appearing inside quotation marks that is actually a paraphrase.

**Repair rule.** Quoted strings in claims must be verbatim from the transcript. If paraphrasing, drop the quotes.

### A20 — Number with bogus precision

**Definition.** A number reported to 3+ significant figures when the source clearly used a round estimate.

**Bad:** "Conversion increases by 27.3%."

**Repair rule.** Round to the precision the source actually used. If the source said "about a quarter", write "~25%" not "25.0%".

---

## Section B — Positive signals (what a load-bearing node looks like)

### B1 — Self-identifying type-label

The node's title or first phrase tells you what kind of node it is. The current graph already has one good example:

> **Mountain Climbing Metaphor** — *Each small mountain climbed increases your capacity for bigger mountains.*

"Metaphor" is in the title. The reader knows immediately not to apply it as a literal rule and that it is a vehicle for an analogy. Other type labels we want: **Framework**, **Mental Model**, **Axis** (quantified gradient), **Anecdote**, **Mechanism** (causal explanation), **Counterexample**, **Definition**, **Contradiction**.

### B2 — Quantified axis with at least two anchors

A number is meaningful when it sits on an axis with at least one other named point:

> **Creative-asset need scales with monthly ad spend.** SMB (<$10k/mo): 5-15 creatives. Mid-market ($10k-$100k/mo): 50-100. Enterprise ($100k+/mo): 200-1000.

The 50-100 becomes interpretable because the surrounding axis frames it.

### B3 — Named entities, named conditions

> Hormozi (Acquisition.com, 2019-2022 cohort, ~60 businesses) found that …

Every load-bearing variable is bound to a named referent.

### B4 — Conditional with mechanism

> X works when Y is true, because Z.

The mechanism statement (Z) is what makes the claim falsifiable: someone can test whether the mechanism is real.

### B5 — Named counterexample

A claim that names where it does *not* apply is more robust than one that doesn't:

> **In B2B service sales ≥$5k, always close on the same call**. (Does not apply to self-serve SaaS or in-store retail, where the call is not the primary decision moment.)

### B6 — Source-grounded with verifiable hook

The reader can find the relevant minute of the transcript:

> source: `helping_e_commerce_business_owners_scale_8C_6qojTA78`, §"Pricing for Mid-Market", roughly 23:40.

---

## Section C — Scoring matrix (use this for every candidate node)

For each candidate node, answer Y/N. A claim must score Y on **all of 1-5** to survive; an example/anecdote must score Y on **1, 2, 4**; a number must score Y on **1, 2, 6**.

1. **Resolvable referents.** Every actor, company, product, time period in the node has a named identity, not a generic role-word.
2. **Standalone meaning.** The node reads correctly out of context, without the previous bullet.
3. **Load-bearing.** If the node were false, the reader's behavior or model would change. (See CLAIM-DEFINITION §1.)
4. **Right type label.** The node's role in the hierarchy is declared (Framework / Claim / Mechanism / Anecdote / Axis / Metaphor / Counterexample / Definition / Contradiction).
5. **Falsifiable.** Some evidence or comparison would settle whether the node is true.
6. **Axis-anchored** (numbers only). Any number is accompanied by its axis and at least one comparison point.
7. **Source-grounded.** `attribution.{speaker, source_file, section}` is present and lookupable.

If a node fails any of its required rows, the agent should choose one of: **rewrite to pass**, **demote to a lower-tier slot** (claim → example, example → implementation detail), or **drop**.

---

## How this rubric is used

- **Phase 0 (Diagnostic)** sub-agents read this rubric and use it to label sample nodes as good/bad. Their labeled output becomes the few-shot set (10 positive + 20 negative) for downstream extraction agents.
- **Phase 3 (Extraction v2)** sub-agents have an abridged Section A + Section C in their system prompt, with the 30 few-shot examples appended. They produce nodes that score 5+/5+ on the matrix.
- **Phase 6 (Validation)** sub-agents independently score a random sample of the new graph against the matrix. Cutoff: ≥80% of sampled claim nodes must score 5/5; ≥90% of sampled example/number nodes must score on their required rows. Below cutoff → re-run extraction.
