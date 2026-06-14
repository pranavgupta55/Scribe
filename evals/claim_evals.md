# Claim Quality Evals — few-shot bank for the Scribe extraction pipeline

This file is **both documentation and the few-shot bank** that gets baked into the
`process.py` extraction prompts. Every example below was graded against the rubric by
reading the one source transcript
(`transcripts/the_lazy_way_i_make_money_with_ai_2026.txt`) and comparing what was
*actually said* to what the old pipeline *stored*.

The whole point: qwen3:1.7b is tiny. It cannot infer "what makes a good claim" from an
abstract instruction. It learns far better from concrete graded pairs. So we show it
real GOOD claims (with the reason they're good) and real BAD claims (with the reason
they're bad), drawn from its own prior output.

---

## The rubric

A stored claim is scored on five binary criteria. Keep it only if it scores **4-5**.

1. **Grounded** — every number, name, and fact is literally present in the transcript.
   Nothing inferred, generalized, or invented.
2. **Specific** — carries a concrete payload: a number, a named entity, a named
   mechanism, or a named outcome. Not a vague gesture ("drives revenue", "is effective").
3. **Standalone** — understandable without the surrounding sentence. No dangling "this",
   "the example", "it" with no referent.
4. **Conclusion-bearing** — it lands a *takeaway the speaker is actually making*, not a
   stray fragment of narration ("the Outreach Specialist is called a database
   reactivation" is a label, not a takeaway).
5. **Useful context** — where a bare number would be cryptic, it carries just enough
   surrounding context *from the transcript* to make the takeaway land. (Short is fine
   when the number speaks for itself.)

> The model must NEVER add context it invents. "Useful context" means **more words
> pulled from the transcript**, never more words pulled from the model's own knowledge.

---

## POSITIVE examples (score 5 — store these, imitate this style)

> **G1.** "A franchise owner named Rob referred over 26 Alloy gym owners and added more
> than $30,000 per month to the agency, because franchise owners sit on advisory boards
> and refer each other freely."
> *Why it's good:* grounded (Rob, 26, Alloy, $30k are all in the transcript), specific
> (named person + two numbers), standalone, and it lands the real takeaway — *reaching
> out to franchise owners is worthwhile because one referral cascades into many.* The
> trailing clause is extra context pulled straight from the transcript, not invented.

> **G2.** "AI follows up with interested leads within five minutes instead of the typical
> 42 hours, which increases the chance of converting that lead by 400%."
> *Why it's good:* short, but every number is concrete and grounded, and the 42-hour
> baseline gives the 400% figure its meaning. A number-driven claim does not need padding.

> **G3.** "A gym owner with four locations got 32 new memberships and 12 personal-training
> agreements in 8 days — a 92% conversion rate — purely by reactivating her existing
> customer database, with zero ad spend."
> *Why it's good:* the '92% conversion' is meaningless alone; the surrounding context
> (former-member list, 8 days, no ad spend) — all from the transcript — makes it land the
> takeaway: *database reactivation beats spending on new marketing.*

> **G4.** "Local US brick-and-mortar businesses miss 62% of inbound calls from customers
> who want to pay, so an AI receptionist that texts back after 10 unanswered seconds
> recovers booked appointments they would otherwise lose."
> *Why it's good:* pairs the problem statistic (62%) with the specific mechanism (text
> back after 10 seconds) and the outcome (recovered bookings) — all grounded.

> **G5.** "Targeting franchise owners let the speaker sign over 200 gym owners without any
> outreach, after one client (Jeff O'Meara, an Anytime Fitness owner with 12 locations)
> referred him across his advisory board in 2019."
> *Why it's good:* the headline number (200, no outreach) is anchored to the named,
> dated, concrete origin story, so it reads as a verifiable conclusion rather than a brag.

---

## NEGATIVE examples (score 0-2 — these were actually stored; never store claims like these)

> **B1.** "169,000 potential customers → value → niche research rubric"
> *Why it's bad:* not a sentence, not standalone, not a conclusion — it's a mangled
> triple masquerading as a claim. The reader cannot tell what is being asserted.
> *Fix:* "The niche-research rubric flagged pet grooming as a strong niche: over 169,000
> potential customers and recurring ~$200/month spend, well above the 10,000-customer bar."

> **B2.** "AI reaches out to past clients with personalized offers, driving high response
> rates and revenue."
> *Why it's bad:* vague verbs ("driving high response rates and revenue") with no number,
> no named mechanism. It's a generic marketing platitude that could describe any product.
> *Fix:* "Sending past clients a personalized, conversational message (instead of a bare
> link) gets a high response rate because recipients think they're talking to a human."

> **B3.** "The five AI workers I hire are paid by customers and can be replaced by cheaper
> freelancers."
> *Why it's bad:* it INVERTS the transcript. The speaker says a single automation is what
> can be replaced by a cheaper freelancer; five workers sold together are a "department"
> nobody can easily replace. This is a hallucinated contradiction of the source. Fails
> "grounded" outright — the worst failure mode.
> *Fix:* "Selling five AI workers together as one 'department' is harder for a client to
> fire than a single automation, which a cheaper freelancer could replace."

> **B4.** "the Outreach Specialist is called a database reactivation"
> *Why it's bad:* a bare label/definition, not a takeaway. Fails "conclusion-bearing".
> The instruction "do not write definitions" exists precisely to kill claims like this.

> **B5.** "Christina's review highlights the effectiveness of AI in improving marketing and
> follow-up operations."
> *Why it's bad:* reports that a review *exists* and is vaguely positive. No payload — what
> did the AI actually do, by how much? Fails "specific". Meta-narration about the video,
> not knowledge from it.
> *Fix:* drop it, or replace with the concrete result the review describes if one is stated.

> **B6.** "AI is strategically used in marketing and follow-up systems to save time for
> business owners."
> *Why it's bad:* pure filler. Every content word is generic. Could be auto-generated for
> any SaaS. Fails "specific" and "conclusion-bearing".

> **B7.** "the example of a text message from a gym/salon shows the problem with
> unpersonalized messages"
> *Why it's bad:* "the example of" — dangling referent, fails "standalone". Also lowercase
> fragment, reads like a note-to-self.
> *Fix:* "Unpersonalized reactivation texts that just send a link get recipients to reply
> 'STOP' nine times out of ten, so they drive no revenue."

---

## How these feed the pipeline

- **Extraction prompt** embeds a compressed GOOD/BAD contrast (G1, G2, B2, B3, B4) so the
  draft step imitates the right shape.
- **Filter/verify step** uses the rubric (5 criteria) as an explicit checklist to drop or
  rewrite each drafted claim, citing the transcript span.
- **The "grounded" + "never invert the source" rule (see B3) is the single most important
  guard** for a 1.7B model and is repeated in every system prompt.
